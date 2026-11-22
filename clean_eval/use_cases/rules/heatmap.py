"""The heat map's rules: the four aspects, the overall score and its bands, a module's rating, a reviewer's levels,
and the convention of the README section."""

import hashlib
import json
import math
import re
from typing import Any, Literal, Optional

from clean_eval.domain.policy import ASPECTS, BAND_NAMES, BANDS, Band, Policy
from clean_eval.domain.rating import (
    AspectScores,
    Evidence,
    Facts,
    Heatmap,
    Judgement,
    ModuleRating,
    ModuleTests,
    Scores,
    TopFile,
)

# Combine aspect scores into the overall score, and map scores and levels to bands.


def clamp(
    value: float,
    low: float = 0.0,
    high: float = 1.0,
) -> float:
    return max(low, min(high, value))


def overall(
    scores: AspectScores,
    peak_share: float = 0.5,
) -> int:
    """(1 - peak_share) of the geometric mean of impact and weakness, plus peak_share of the worst single aspect.
    `scores` (AspectScores or Scores) holds biz, effects, entropy, unverified; an N/A aspect (None) is left out."""
    impact = max(scores.biz, scores.effects)
    rated = [v for v in (scores.entropy, scores.unverified) if v is not None]
    weakness = sum(rated) / len(rated)
    worst = max(v for v in (scores.biz, scores.effects, scores.entropy, scores.unverified) if v is not None)
    return round((1 - peak_share) * math.sqrt(impact * weakness) + peak_share * worst)


def level_range(
    level: int,
) -> tuple[int, int]:
    """Score range of a level: level 0 is the top band, level 3 the bottom one."""
    idx = len(BANDS) - 1 - level
    return (BANDS[idx - 1][0] + 1 if idx else 0), BANDS[idx][0]


def place_in_level(
    level: int,
    static_score: int,
) -> int:
    """The level fixes the band; the static score (0-100) positions the module inside it."""
    lo, hi = level_range(level)
    return round(lo + (hi - lo) * max(0, min(100, static_score)) / 100)


def band(
    score: float,
) -> Band:
    for upper, name in BANDS:
        if score <= upper:
            return name
    return BANDS[-1][1]


def band_floor(
    name: str,
) -> int:
    """Lowest score of a band."""
    for i, (_, n) in enumerate(BANDS):
        if n == name:
            return BANDS[i - 1][0] + 1 if i else 0
    raise ValueError("unknown band %r" % name)


def severity(
    name: Optional[str],
) -> int:
    """Order of bands, 0 for Low; None (unknown) sorts lowest."""
    return BAND_NAMES.index(name) if name else -1


# The four risk aspects. Each is a pure function of one module's Facts and the Policy, and returns
# (score 0-100, evidence lines).

FLOW_TERMS = ("flow_weight", "entry")


def business(
    facts: Facts,
    policy: Policy,
) -> tuple[float, list[str]]:
    w, sat = dict(policy.business_weights), policy.business_saturation
    entry = max(facts.entry_flows, default=0.0)
    terms = {
        "dependents": clamp(facts.fan_in / sat["dependents"]),
        "flow_weight": clamp(facts.flow_touch / sat["flow_weight"]),
        "entry": entry,
        "keyword": 1.0 if facts.keyword else 0.0,
        "size": clamp(facts.share / sat["size"]),
    }
    ev = []
    if not facts.flows_available:  # no flow data: the other terms carry the flow terms' weight, instead of scoring 0
        total = sum(w.values())
        w = {k: v for k, v in w.items() if k not in FLOW_TERMS}
        scale = total / (sum(w.values()) or 1)
        w = {k: v * scale for k, v in w.items()}
    score = 100 * sum(v * terms[k] for k, v in w.items())
    if facts.tooling:
        score *= policy.tooling_factor
        ev.append("Tooling path: business impact x %g" % policy.tooling_factor)
    if facts.flows_available:
        ev.append(
            "%d modules depend on it; entry point of %d flows (top criticality %.2f); on %d%% of flow weight; %d%% of repo code"
            % (facts.fan_in, len(facts.entry_flows), entry, round(facts.flow_touch * 100), round(facts.share * 100))
        )
    else:
        ev.append(
            "%d modules depend on it; %d%% of repo code; no execution-flow data, so dependents, name and size carry the score"
            % (facts.fan_in, round(facts.share * 100))
        )
    if facts.keyword:
        ev.append("Name matches a core-flow keyword (pipeline, api, agent, ...)")
    return score, ev


def effects(
    facts: Facts,
    policy: Policy,
) -> tuple[float, list[str]]:
    points = (
        sum(policy.effect_points.get(k, 0) * v for k, v in facts.effect_counts.items())
        + policy.migration_points * facts.migrations
    )
    mix = policy.effects_mix
    score = 100 * (
        mix["points"] * (1 - math.exp(-points / policy.effects_scale))
        + mix["dependents"] * clamp(facts.fan_in / policy.effects_dependents_saturation)
    )
    ev = [
        "%d %s call sites (e.g. %s)" % (v, k.replace("_", " "), facts.effect_examples.get(k, "?"))
        for k, v in facts.effect_counts.most_common(3)
    ]
    if facts.migrations:
        ev.append("%d migration files (schema changes are hard to reverse)" % facts.migrations)
    ev.append("%d other modules import or call it" % facts.fan_in)
    return score, ev


def entropy(
    facts: Facts,
    policy: Policy,
) -> tuple[float, list[str]]:
    w, sat = policy.entropy_weights, policy.entropy_saturation
    n = len(facts.funcs)
    maxfn = max((x[0] for x in facts.funcs), default=0)
    long_share = sum(1 for x in facts.funcs if x[0] > policy.long_func_lines) / n if n else 0
    cx_share = sum(1 for x in facts.funcs if x[1] > policy.complex_func) / n if n else 0
    avg_cx = sum(x[1] for x in facts.funcs) / n if n else 0
    parts = {
        "long_funcs": clamp(long_share / sat["long_funcs"]),
        "longest_func": clamp((maxfn - policy.longest_free) / policy.longest_range)
        if n
        else policy.neutral_no_functions,
        "complex_funcs": clamp(cx_share / sat["complex_funcs"]) if n else policy.neutral_no_functions,
        "avg_complexity": clamp((avg_cx - policy.avg_complexity_free) / policy.avg_complexity_range)
        if n
        else policy.neutral_no_functions,
        "exceptions": clamp((facts.broad + 2 * facts.swallow) / facts.kloc / sat["exceptions_per_k"]),
        "untyped": 1 - facts.typed_share if facts.typed_share is not None else policy.neutral_untyped,
        "coupling": clamp(facts.fan_out / sat["coupling"]),
        "big_files": clamp(facts.big_files / max(facts.files, 1) / sat["big_files"]),
        "todo": clamp(facts.todo / facts.kloc / sat["todo_per_k"]),
    }
    score = 100 * sum(w[k] * parts[k] for k in w)
    ev = []
    if n:
        ev.append(
            "%d functions: %d%% over %d lines, longest %d lines, %d%% over complexity %d"
            % (n, round(long_share * 100), policy.long_func_lines, maxfn, round(cx_share * 100), policy.complex_func)
        )
        ev.append(
            "%d broad and %d swallowed exceptions; %d%% of functions fully typed"
            % (facts.broad, facts.swallow, round((facts.typed_share or 0) * 100))
        )
    else:
        ev.append("No functions to measure (constants, models or empty files); size terms use a neutral value")
    ev.append(
        "Depends on %d other modules; %d files over %d lines; %d TODO markers"
        % (facts.fan_out, facts.big_files, policy.big_file_lines, facts.todo)
    )
    return score, ev


def unverified(
    facts: Facts,
    effects_score: float,
    policy: Policy,
) -> tuple[Optional[float], list[str], dict[str, Any]]:
    """Returns (score or None when N/A, evidence, info); info is the per-module test summary."""
    ratio = facts.linked_test_loc / max(facts.loc, 1)
    coverage = facts.linked_functions / facts.graph_functions if facts.graph_functions else None
    info = {
        "functions": facts.graph_functions,
        "tested": facts.linked_functions,
        "coverage": round(coverage * 100) if coverage is not None else None,
        "files": facts.linked_test_files,
        "loc": round(facts.linked_test_loc),
        "ratio": round(ratio, 2),
        "scales": dict(facts.test_scales),
    }
    if facts.na_reason:
        return None, ["Not applicable: %s; not rated and left out of the overall score" % facts.na_reason], info
    ev = []
    if coverage is None:
        score = policy.unverified_no_functions
        ev.append("No functions in the graph; nothing to test")
    else:
        mix = policy.unverified_mix
        score = mix["links"] * 100 * (1 - coverage) + mix["ratio"] * 100 * (
            1 - clamp(ratio / policy.unverified_ratio_target)
        )
        ev.append(
            "%d of %d functions are linked to tests in the graph" % (facts.linked_functions, facts.graph_functions)
        )
        ev.append(
            "%d lines in the linked test files, shared across the modules they cover (ratio %.2f to %d source lines)"
            % (round(facts.linked_test_loc), ratio, facts.loc)
        )
    if facts.line_rate is not None:
        score = 0.5 * score + 0.5 * 100 * (1 - facts.line_rate)
        ev.append("Coverage report: %d%% line rate" % round(facts.line_rate * 100))
    if facts.linked_functions and sum(1 for k in ("unit", "integration", "e2e") if facts.test_scales.get(k)) <= 1:
        score += policy.single_scale_penalty
    if (
        effects_score >= policy.writes_threshold
        and not facts.test_scales.get("integration")
        and not facts.test_scales.get("e2e")
    ):
        score += policy.no_integration_penalty
        ev.append("Writes or is widely depended on but no integration or e2e test is linked")
    if facts.test_scales:
        ev.append("Linked test files by scale: " + ", ".join("%s %d" % kv for kv in facts.test_scales.most_common()))
    return clamp(score, 0, 100), ev, info


# Rate one module: run the four aspect rules over its facts.


def to_score(
    value: float,
) -> int:
    """A rule's raw result as a score: rounded, and kept to 0-100 (weights tuned to sum past 1 could overshoot)."""
    return min(100, max(0, round(value)))


def score_facts(
    facts: Facts,
    policy: Policy,
) -> tuple[Scores, Evidence, ModuleTests]:
    """(Scores, Evidence, ModuleTests) of one module's `facts` under `policy`."""
    biz, biz_ev = business(facts, policy)
    eff, eff_ev = effects(facts, policy)
    ent, ent_ev = entropy(facts, policy)
    unv, unv_ev, info = unverified(facts, eff, policy)
    aspect = AspectScores(
        biz=to_score(biz),
        effects=to_score(eff),
        entropy=to_score(ent),
        unverified=to_score(unv) if unv is not None else None,
    )
    scores = Scores(**aspect.model_dump(), overall=overall(aspect, policy.overall_peak_share))
    return scores, Evidence(biz=biz_ev, effects=eff_ev, entropy=ent_ev, unverified=unv_ev), ModuleTests(**info)


def rate_module(
    facts: Facts,
    top_files: list[TopFile],
    policy: Policy,
) -> ModuleRating:
    scores, evidence, tests = score_facts(facts, policy)
    return ModuleRating(
        name=facts.name,
        loc=facts.loc,
        files=facts.files,
        scores=scores,
        baseline=AspectScores(**scores.model_dump(exclude={"overall"})),
        tests=tests,
        evidence=evidence,
        top_files=top_files,
        facts=facts,
    )


def rescore(
    facts: Facts,
    policy: Policy,
) -> Scores:
    """Static scores of a module from its stored Facts under another policy: the basis of calibration replays."""
    return score_facts(facts, policy)[0]


# Apply a reviewer's levels to a rated heatmap: the level fixes each aspect's band, the static score places it inside.


def apply_levels(
    heatmap: Heatmap,
    judgement: Judgement,
) -> Heatmap:
    """Apply a Judgement (its entries are already validated). N/A aspects are left alone."""
    judged = 0
    for m in heatmap.modules:
        levels = judgement.modules.get(m.name, {})
        hit = False
        for name, key, _, _, texts in ASPECTS:
            entry = levels.get(name)
            if entry is None or getattr(m.scores, key) is None:
                continue
            setattr(m.scores, key, place_in_level(entry.level, getattr(m.baseline, key)))
            getattr(m.evidence, key).insert(0, "Level %d, %s: %s" % (entry.level, texts[entry.level], entry.reason))
            hit = True
        judged += hit
    heatmap.judged_modules = judged
    return heatmap


def refresh_overall(
    heatmap: Heatmap,
    peak_share: float = 0.5,
) -> Heatmap:
    for m in heatmap.modules:
        m.scores.overall = overall(m.scores, peak_share)
    return heatmap


# The README convention: the heat map lives in a `## Risk Heat Map` section, between two markers, so the next run can
# replace it. A fingerprint of the ratings inside the block tells whether a new run changes anything.

MARKERS = ("<!-- risk-heat-map:start -->", "<!-- risk-heat-map:end -->")
SECTION_HEADING = "## Risk Heat Map"
FINGERPRINT = re.compile(r"<!-- risk-heat-map:fingerprint=([0-9a-f]{12}) -->")


def fingerprint(
    heatmap: Heatmap,
) -> str:
    """Stable id of what the diagram shows (modules, sizes, scores), independent of the commit and the date."""
    shown = sorted((m.name, m.loc, sorted(m.scores.model_dump().items())) for m in heatmap.modules)
    return hashlib.sha1(json.dumps(shown).encode()).hexdigest()[:12]


def block_fingerprint(
    readme_text: Optional[str],
) -> Optional[str]:
    """The fingerprint inside the README's heat-map block, or None."""
    if not readme_text or MARKERS[0] not in readme_text:
        return None
    block = readme_text.split(MARKERS[0], 1)[1].split(MARKERS[1], 1)[0]
    m = FINGERPRINT.search(block)
    return m.group(1) if m else None


def merge_readme(
    existing: Optional[str],
    block: str,
) -> tuple[str, Literal["ADD", "UPDATE"]]:
    """Put `block` (markers included) into README text: between existing markers, else under the section heading
    (replacing that section's old body), else as a new section at the end. Returns (new text, ADD or UPDATE)."""
    start, end = MARKERS
    if existing and start in existing and end in existing:
        head, rest = existing.split(start, 1)
        return head + block.rstrip("\n") + rest.split(end, 1)[1], "UPDATE"
    text = existing or ""
    m = re.search(r"^%s[ \t]*$" % re.escape(SECTION_HEADING), text, re.M)
    if m:
        nxt = re.search(r"^## ", text[m.end() :], re.M)
        stop = m.end() + nxt.start() if nxt else len(text)
        tail = text[stop:].lstrip("\n")
        return text[: m.end()] + "\n\n" + block.rstrip("\n") + "\n" + ("\n" + tail if tail else ""), "UPDATE"
    prefix = text.rstrip("\n") + "\n\n" if text.strip() else ""
    return prefix + SECTION_HEADING + "\n\n" + block.rstrip("\n") + "\n", "ADD"
