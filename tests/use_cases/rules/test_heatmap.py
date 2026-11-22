"""The heat map's rules (use_cases/rules/heatmap.py): scores and bands, the four aspects, rating a module, a
reviewer's levels, and the README convention."""

from collections import Counter

import pytest

from clean_eval.domain.policy import ASPECTS, Policy
from clean_eval.domain.rating import AspectScores, Facts, Judgement
from clean_eval.use_cases.rules.heatmap import (
    MARKERS,
    SECTION_HEADING,
    apply_levels,
    band,
    band_floor,
    block_fingerprint,
    business,
    effects,
    entropy,
    fingerprint,
    level_range,
    merge_readme,
    overall,
    place_in_level,
    rate_module,
    refresh_overall,
    rescore,
    unverified,
)
from tests.domain.builders import facts, heatmap, rating

# Scoring (use_cases/rules/heatmap.py): bands, levels and the overall score.


def S(biz, effects, entropy, unverified):
    return AspectScores(biz=biz, effects=effects, entropy=entropy, unverified=unverified)


def test_one_outstanding_aspect_lifts_overall():
    assert overall(S(95, 0, 0, 0)) == 48
    assert overall(S(0, 0, 0, 100)) == 50


def test_peak_share_moves_weight_to_the_worst_aspect():
    s = S(95, 0, 0, 0)
    assert overall(s, peak_share=1.0) == 95
    assert overall(s, peak_share=0.0) == 0


def test_not_applicable_aspect_is_left_out_of_overall():
    assert overall(S(40, 10, 20, None)) == overall(S(40, 10, 20, 20))


def test_levels_map_to_bands_most_severe_first():
    assert [level_range(n) for n in range(4)] == [(75, 100), (50, 74), (25, 49), (0, 24)]
    assert [place_in_level(0, s) for s in (0, 100)] == [75, 100]
    assert [band(place_in_level(n, 55)) for n in range(4)] == ["Critical", "High", "Moderate", "Low"]


def test_band_edges_and_floors():
    assert [band(v) for v in (0, 24, 25, 49, 50, 74, 75, 100)] == [
        "Low",
        "Low",
        "Moderate",
        "Moderate",
        "High",
        "High",
        "Critical",
        "Critical",
    ]
    assert [band_floor(b) for b in ("Low", "Moderate", "High", "Critical")] == [0, 25, 50, 75]


# The four risk aspects (use_cases/rules/heatmap.py): Business Impact, Side Effects, Entropy and Unverified.

P = Policy()


def test_tooling_scales_business_score():
    plain, _ = business(facts(fan_in=6, keyword=True), P)
    tool, ev = business(facts(fan_in=6, keyword=True, tooling=True), P)
    assert tool == pytest.approx(plain * 0.5)
    assert ev[0].startswith("Tooling path")


def test_business_without_flow_data_rescales_the_other_terms():
    with_flows, _ = business(facts(fan_in=6, keyword=True, share=0.25), P)
    no_flows, ev = business(facts(fan_in=6, keyword=True, share=0.25, flows_available=False), P)
    assert with_flows == pytest.approx(55.0)  # dependents, keyword and size at full strength; no flows
    assert no_flows == pytest.approx(100.0)  # the same terms carry the whole weight
    assert any("no execution-flow data" in e for e in ev)


def test_entropy_without_functions_uses_neutral_terms():
    score, ev = entropy(facts(funcs=[], typed_share=None), P)
    assert any("No functions to measure" in e for e in ev)
    assert 0 < score < 40


def test_effects_grow_with_writes_and_migrations():
    quiet, _ = effects(facts(), P)
    busy, ev = effects(
        facts(effect_counts=Counter(db_write=10), effect_examples={"db_write": "a.py:1"}, migrations=3, fan_in=4), P
    )
    assert busy > quiet + 40
    assert any("migration" in e for e in ev)


def test_unverified_rules():
    neutral, _, info = unverified(
        facts(graph_functions=0, linked_functions=0, linked_test_loc=0.0, test_scales=Counter()), 0, P
    )
    assert (neutral, info["coverage"]) == (P.unverified_no_functions, None)
    base, _, _ = unverified(facts(test_scales=Counter(unit=3)), 10, P)
    risky, ev, _ = unverified(facts(test_scales=Counter(unit=3)), 80, P)
    assert base is not None
    assert risky == base + P.no_integration_penalty
    untested, _, _ = unverified(
        facts(linked_functions=0, linked_test_files=0, linked_test_loc=0.0, test_scales=Counter()), 0, P
    )
    assert untested is not None
    assert untested >= 99
    na, ev, _ = unverified(facts(na_reason="Infrastructure manifest"), 90, P)
    assert na is None
    assert ev[0].startswith("Not applicable")


# Rating one module (use_cases/rules/heatmap.py) from its facts, and rescoring it under another policy.


def test_stored_facts_reproduce_the_rating():
    f = facts(effect_counts=Counter(db_write=2), test_scales=Counter(unit=1), funcs=[(70, 18), (5, 1)])
    r = rate_module(f, [], P)
    assert r.facts is not None
    stored = Facts.model_validate_json(r.facts.model_dump_json())  # as data.json or a history keeps them
    assert stored == f
    assert rescore(stored, P) == r.scores
    assert r.baseline.model_dump() == r.scores.model_dump(exclude={"overall"})
    louder = Policy.from_overrides({"effect_points": {"db_write": 30}})
    assert rescore(stored, louder).effects > r.scores.effects


def test_scores_stay_in_range_when_tuned_weights_sum_past_one():
    heavy = Policy.from_overrides({"business_weights": {"dependents": 0.9, "keyword": 0.9, "size": 0.9}})
    r = rate_module(facts(fan_in=12, keyword=True, share=1.0), [], heavy)
    assert r.scores.biz == 100


# Applying a reviewer's levels (use_cases/rules/heatmap.py): each level places a score within its band.


def levels(raw):
    return Judgement.model_validate(raw)


def test_every_aspect_level_sets_its_band_and_the_static_score_places_inside_it():
    for name, key, *_ in ASPECTS:
        for level, (lo, hi) in enumerate(((75, 100), (50, 74), (25, 49), (0, 24))):
            h = apply_levels(heatmap(rating()), levels({"modules": {"m": {name: {"level": level, "reason": "r"}}}}))
            m = h.modules[0]
            assert lo <= getattr(m.scores, key) <= hi, (name, level, getattr(m.scores, key))
            assert getattr(m.evidence, key)[0].startswith("Level %d" % level)
    m = apply_levels(
        heatmap(rating()), levels({"modules": {"m": {"business": {"level": 1, "reason": "core"}}}})
    ).modules[0]
    assert (m.scores.biz, band(m.scores.biz)) == (60, "High")


@pytest.mark.parametrize("bad", ["high", 4, -1, 1.5, True, None, [1], {"a": 1}], ids=repr)
def test_an_invalid_level_is_ignored(bad):
    h = apply_levels(
        heatmap(rating()),
        levels({"modules": {"m": {"business": {"level": bad}}, "nope": {"business": {"level": 0}}}}),
    )
    assert h.modules[0].scores.biz == 40
    assert h.judged_modules == 0


@pytest.mark.parametrize("bad", [[], "x", {"modules": []}, {"modules": {"m": "x"}}], ids=repr)
def test_a_malformed_judgement_is_ignored(bad):
    assert apply_levels(heatmap(rating()), levels(bad)).judged_modules == 0


def test_a_level_for_a_not_applicable_aspect_is_ignored():
    h = apply_levels(heatmap(rating(unverified=None)), levels({"modules": {"m": {"unverified": {"level": 0}}}}))
    assert h.modules[0].scores.unverified is None


def test_overall_is_refreshed_with_the_policy_share():
    h = heatmap(rating())
    h.modules[0].scores.overall = 0
    assert refresh_overall(h, 0.5).modules[0].scores.overall == overall(h.modules[0].scores)


# The README convention (use_cases/rules/heatmap.py): the heat-map block between its markers, and its fingerprint.

BLOCK = MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"


def test_block_is_appended_with_a_heading_or_replaced_between_markers():
    text, action = merge_readme("# Title\n\nIntro.\n", BLOCK)
    assert action == "ADD"
    assert text.startswith("# Title\n\nIntro.\n\n" + SECTION_HEADING + "\n\n")
    old = "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"
    text, action = merge_readme(old, BLOCK)
    assert action == "UPDATE"
    assert "new" in text
    assert "old table" not in text
    assert text.endswith("\n\n## Next\nkeep\n")
    assert merge_readme(None, BLOCK)[0] == SECTION_HEADING + "\n\n" + BLOCK


def test_heading_without_markers_gets_its_body_replaced():
    text, action = merge_readme("# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n", BLOCK)
    assert action == "UPDATE"
    assert "stale" not in text
    assert text.count(SECTION_HEADING) == 1
    assert text.endswith("## Next\nkeep\n")


def test_fingerprint_ignores_commit_and_date_but_not_scores():
    a, b = heatmap(rating()), heatmap(rating())
    b.sha, b.generated_at = "fff0000", "2030-01-01"
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(heatmap(rating(biz=41)))
    assert block_fingerprint("x\n" + BLOCK) == "0123456789ab"
    assert block_fingerprint("no block") is None
