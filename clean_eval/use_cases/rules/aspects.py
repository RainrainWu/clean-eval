"""The four risk aspects. Each is a pure function of one module's Facts and the Policy, and returns
(score 0-100, evidence lines)."""

import math

from clean_eval.use_cases.rules.scoring import clamp

FLOW_TERMS = ("flow_weight", "entry")


def business(f, p):
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


def effects(f, p):
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


def entropy(f, p):
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


def unverified(f, effects_score, p):
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
