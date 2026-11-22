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


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def overall(s: AspectScores, peak_share: float = 0.5) -> int:
    """(1 - peak_share) of the geometric mean of impact and weakness, plus peak_share of the worst single aspect.
    `s` (AspectScores or Scores) holds biz, effects, entropy, unverified; an N/A aspect (None) is left out."""
    impact = max(s.biz, s.effects)
    rated = [v for v in (s.entropy, s.unverified) if v is not None]
    weakness = sum(rated) / len(rated)
    worst = max(v for v in (s.biz, s.effects, s.entropy, s.unverified) if v is not None)
    return round((1 - peak_share) * math.sqrt(impact * weakness) + peak_share * worst)


def level_range(level: int) -> tuple[int, int]:
    """Score range of a level: level 0 is the top band, level 3 the bottom one."""
    idx = len(BANDS) - 1 - level
    return (BANDS[idx - 1][0] + 1 if idx else 0), BANDS[idx][0]


def place_in_level(level: int, static_score: int) -> int:
    """The level fixes the band; the static score (0-100) positions the module inside it."""
    lo, hi = level_range(level)
    return round(lo + (hi - lo) * max(0, min(100, static_score)) / 100)


def band(score: float) -> Band:
    for upper, name in BANDS:
        if score <= upper:
            return name
    return BANDS[-1][1]


def band_floor(name: str) -> int:
    """Lowest score of a band."""
    for i, (_, n) in enumerate(BANDS):
        if n == name:
            return BANDS[i - 1][0] + 1 if i else 0
    raise ValueError("unknown band %r" % name)


def severity(name: Optional[str]) -> int:
    """Order of bands, 0 for Low; None (unknown) sorts lowest."""
    return BAND_NAMES.index(name) if name else -1


# The four risk aspects. Each is a pure function of one module's Facts and the Policy, and returns
# (score 0-100, evidence lines).

FLOW_TERMS = ("flow_weight", "entry")


def business(f: Facts, p: Policy) -> tuple[float, list[str]]:
    w, sat = dict(p.business_weights), p.business_saturation
    entry = max(f.entry_flows, default=0.0)
    terms = {
        "dependents": clamp(f.fan_in / sat["dependents"]),
        "flow_weight": clamp(f.flow_touch / sat["flow_weight"]),
        "entry": entry,
        "keyword": 1.0 if f.keyword else 0.0,
        "size": clamp(f.share / sat["size"]),
    }
    ev = []
    if not f.flows_available:  # no flow data: the other terms carry the flow terms' weight, instead of scoring 0
        total = sum(w.values())
        w = {k: v for k, v in w.items() if k not in FLOW_TERMS}
        scale = total / (sum(w.values()) or 1)
        w = {k: v * scale for k, v in w.items()}
    score = 100 * sum(v * terms[k] for k, v in w.items())
    if f.tooling:
        score *= p.tooling_factor
        ev.append("Tooling path: business impact x %g" % p.tooling_factor)
    if f.flows_available:
        ev.append(
            "%d modules depend on it; entry point of %d flows (top criticality %.2f); on %d%% of flow weight; %d%% of repo code"
            % (f.fan_in, len(f.entry_flows), entry, round(f.flow_touch * 100), round(f.share * 100))
        )
    else:
        ev.append(
            "%d modules depend on it; %d%% of repo code; no execution-flow data, so dependents, name and size carry the score"
            % (f.fan_in, round(f.share * 100))
        )
    if f.keyword:
        ev.append("Name matches a core-flow keyword (pipeline, api, agent, ...)")
    return score, ev


def effects(f: Facts, p: Policy) -> tuple[float, list[str]]:
    points = sum(p.effect_points.get(k, 0) * v for k, v in f.effect_counts.items()) + p.migration_points * f.migrations
    mix = p.effects_mix
    score = 100 * (
        mix["points"] * (1 - math.exp(-points / p.effects_scale))
        + mix["dependents"] * clamp(f.fan_in / p.effects_dependents_saturation)
    )
    ev = [
        "%d %s call sites (e.g. %s)" % (v, k.replace("_", " "), f.effect_examples.get(k, "?"))
        for k, v in f.effect_counts.most_common(3)
    ]
    if f.migrations:
        ev.append("%d migration files (schema changes are hard to reverse)" % f.migrations)
    ev.append("%d other modules import or call it" % f.fan_in)
    return score, ev


def entropy(f: Facts, p: Policy) -> tuple[float, list[str]]:
    w, sat = p.entropy_weights, p.entropy_saturation
    n = len(f.funcs)
    maxfn = max((x[0] for x in f.funcs), default=0)
    long_share = sum(1 for x in f.funcs if x[0] > p.long_func_lines) / n if n else 0
    cx_share = sum(1 for x in f.funcs if x[1] > p.complex_func) / n if n else 0
    avg_cx = sum(x[1] for x in f.funcs) / n if n else 0
    parts = {
        "long_funcs": clamp(long_share / sat["long_funcs"]),
        "longest_func": clamp((maxfn - p.longest_free) / p.longest_range) if n else p.neutral_no_functions,
        "complex_funcs": clamp(cx_share / sat["complex_funcs"]) if n else p.neutral_no_functions,
        "avg_complexity": clamp((avg_cx - p.avg_complexity_free) / p.avg_complexity_range)
        if n
        else p.neutral_no_functions,
        "exceptions": clamp((f.broad + 2 * f.swallow) / f.kloc / sat["exceptions_per_k"]),
        "untyped": 1 - f.typed_share if f.typed_share is not None else p.neutral_untyped,
        "coupling": clamp(f.fan_out / sat["coupling"]),
        "big_files": clamp(f.big_files / max(f.files, 1) / sat["big_files"]),
        "todo": clamp(f.todo / f.kloc / sat["todo_per_k"]),
    }
    score = 100 * sum(w[k] * parts[k] for k in w)
    ev = []
    if n:
        ev.append(
            "%d functions: %d%% over %d lines, longest %d lines, %d%% over complexity %d"
            % (n, round(long_share * 100), p.long_func_lines, maxfn, round(cx_share * 100), p.complex_func)
        )
        ev.append(
            "%d broad and %d swallowed exceptions; %d%% of functions fully typed"
            % (f.broad, f.swallow, round((f.typed_share or 0) * 100))
        )
    else:
        ev.append("No functions to measure (constants, models or empty files); size terms use a neutral value")
    ev.append(
        "Depends on %d other modules; %d files over %d lines; %d TODO markers"
        % (f.fan_out, f.big_files, p.big_file_lines, f.todo)
    )
    return score, ev


def unverified(f: Facts, effects_score: float, p: Policy) -> tuple[Optional[float], list[str], dict[str, Any]]:
    """Returns (score or None when N/A, evidence, info); info is the per-module test summary."""
    ratio = f.linked_test_loc / max(f.loc, 1)
    coverage = f.linked_functions / f.graph_functions if f.graph_functions else None
    info = {
        "functions": f.graph_functions,
        "tested": f.linked_functions,
        "coverage": round(coverage * 100) if coverage is not None else None,
        "files": f.linked_test_files,
        "loc": round(f.linked_test_loc),
        "ratio": round(ratio, 2),
        "scales": dict(f.test_scales),
    }
    if f.na_reason:
        return None, ["Not applicable: %s; not rated and left out of the overall score" % f.na_reason], info
    ev = []
    if coverage is None:
        score = p.unverified_no_functions
        ev.append("No functions in the graph; nothing to test")
    else:
        mix = p.unverified_mix
        score = mix["links"] * 100 * (1 - coverage) + mix["ratio"] * 100 * (
            1 - clamp(ratio / p.unverified_ratio_target)
        )
        ev.append("%d of %d functions are linked to tests in the graph" % (f.linked_functions, f.graph_functions))
        ev.append(
            "%d lines in the linked test files, shared across the modules they cover (ratio %.2f to %d source lines)"
            % (round(f.linked_test_loc), ratio, f.loc)
        )
    if f.line_rate is not None:
        score = 0.5 * score + 0.5 * 100 * (1 - f.line_rate)
        ev.append("Coverage report: %d%% line rate" % round(f.line_rate * 100))
    if f.linked_functions and sum(1 for k in ("unit", "integration", "e2e") if f.test_scales.get(k)) <= 1:
        score += p.single_scale_penalty
    if effects_score >= p.writes_threshold and not f.test_scales.get("integration") and not f.test_scales.get("e2e"):
        score += p.no_integration_penalty
        ev.append("Writes or is widely depended on but no integration or e2e test is linked")
    if f.test_scales:
        ev.append("Linked test files by scale: " + ", ".join("%s %d" % kv for kv in f.test_scales.most_common()))
    return clamp(score, 0, 100), ev, info


# Rate one module: run the four aspect rules over its facts.


def _score(value: float) -> int:
    """A rule's raw result as a score: rounded, and kept to 0-100 (weights tuned to sum past 1 could overshoot)."""
    return min(100, max(0, round(value)))


def score_facts(f: Facts, p: Policy) -> tuple[Scores, Evidence, ModuleTests]:
    """(Scores, Evidence, ModuleTests) of one module's Facts under policy `p`."""
    biz, biz_ev = business(f, p)
    eff, eff_ev = effects(f, p)
    ent, ent_ev = entropy(f, p)
    unv, unv_ev, info = unverified(f, eff, p)
    aspect = AspectScores(
        biz=_score(biz), effects=_score(eff), entropy=_score(ent), unverified=_score(unv) if unv is not None else None
    )
    scores = Scores(**aspect.model_dump(), overall=overall(aspect, p.overall_peak_share))
    return scores, Evidence(biz=biz_ev, effects=eff_ev, entropy=ent_ev, unverified=unv_ev), ModuleTests(**info)


def rate_module(f: Facts, top_files: list[TopFile], p: Policy) -> ModuleRating:
    scores, evidence, tests = score_facts(f, p)
    return ModuleRating(
        name=f.name,
        loc=f.loc,
        files=f.files,
        scores=scores,
        baseline=AspectScores(**scores.model_dump(exclude={"overall"})),
        tests=tests,
        evidence=evidence,
        top_files=top_files,
        facts=f,
    )


def rescore(facts: Facts, p: Policy) -> Scores:
    """Static scores of a module from its stored Facts under another policy: the basis of calibration replays."""
    return score_facts(facts, p)[0]


# Apply a reviewer's levels to a rated heatmap: the level fixes each aspect's band, the static score places it inside.


def apply_levels(heatmap: Heatmap, judgement: Judgement) -> Heatmap:
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


def refresh_overall(heatmap: Heatmap, peak_share: float = 0.5) -> Heatmap:
    for m in heatmap.modules:
        m.scores.overall = overall(m.scores, peak_share)
    return heatmap


# The README convention: the heat map lives in a `## Risk Heat Map` section, between two markers, so the next run can
# replace it. A fingerprint of the ratings inside the block tells whether a new run changes anything.

MARKERS = ("<!-- risk-heat-map:start -->", "<!-- risk-heat-map:end -->")
SECTION_HEADING = "## Risk Heat Map"
FINGERPRINT = re.compile(r"<!-- risk-heat-map:fingerprint=([0-9a-f]{12}) -->")


def fingerprint(heatmap: Heatmap) -> str:
    """Stable id of what the diagram shows (modules, sizes, scores), independent of the commit and the date."""
    shown = sorted((m.name, m.loc, sorted(m.scores.model_dump().items())) for m in heatmap.modules)
    return hashlib.sha1(json.dumps(shown).encode()).hexdigest()[:12]


def block_fingerprint(readme_text: Optional[str]) -> Optional[str]:
    """The fingerprint inside the README's heat-map block, or None."""
    if not readme_text or MARKERS[0] not in readme_text:
        return None
    block = readme_text.split(MARKERS[0], 1)[1].split(MARKERS[1], 1)[0]
    m = FINGERPRINT.search(block)
    return m.group(1) if m else None


def merge_readme(existing: Optional[str], block: str) -> tuple[str, Literal["ADD", "UPDATE"]]:
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
