"""The heat map's rules (use_cases/rules/heatmap.py): scores and bands, the four aspects, rating a module, a
reviewer's levels, and the README convention."""

from collections import Counter

import pytest

from clean_eval.domain.policy import ASPECTS
from clean_eval.domain.rating import Facts
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

# Aspect scores with one outstanding aspect.
BUSINESS_ONLY = {"biz": 95, "effects": 0, "entropy": 0, "unverified": 0}
UNVERIFIED_ONLY = {"biz": 0, "effects": 0, "entropy": 0, "unverified": 100}
# The bands, ascending: (name, lowest score, highest score).
BAND_EDGES = [("Low", 0, 24), ("Moderate", 25, 49), ("High", 50, 74), ("Critical", 75, 100)]
# A reviewer's levels, most severe first: (level, the score range of its band, the band).
LEVEL_BANDS = [(0, (75, 100), "Critical"), (1, (50, 74), "High"), (2, (25, 49), "Moderate"), (3, (0, 24), "Low")]
# Each aspect at each level, for the `heatmap` fixture's module "m": (judgement, score key, level, score range).
EVERY_LEVEL = [
    ({"modules": {"m": {name: {"level": level, "reason": "r"}}}}, key, level, score_range)
    for name, key, *_ in ASPECTS
    for level, score_range, _ in LEVEL_BANDS
]
EVERY_LEVEL_IDS = ["%s at level %d" % (name, level) for name, *_ in ASPECTS for level, *_ in LEVEL_BANDS]
# Levels that aren't one, each given to module "m" next to a valid level for a module the heat map doesn't have.
INVALID_LEVELS = ["high", 4, -1, 1.5, True, None, [1], {"a": 1}]
INVALID_LEVEL_JUDGEMENTS = [
    {"modules": {"m": {"business": {"level": level}}, "nope": {"business": {"level": 0}}}} for level in INVALID_LEVELS
]
# The heat map's block as `readme` writes it, and READMEs to put it in.
BLOCK = MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"
README = "# Title\n\nIntro.\n"
README_WITH_BLOCK = (
    "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"
)
README_WITHOUT_MARKERS = "# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n"


class TestOverall:
    """The overall score: the geometric mean of impact and weakness and, by the peak share, the worst single aspect;
    an aspect that doesn't apply is left out."""

    @pytest.mark.parametrize(
        ("aspect_scores", "expected"),
        [(BUSINESS_ONLY, 48), (UNVERIFIED_ONLY, 50)],
        indirect=["aspect_scores"],
        ids=["business impact", "unverified"],
    )
    def test_one_outstanding_aspect_lifts_overall(
        self,
        aspect_scores,
        expected,
    ):
        assert overall(aspect_scores) == expected

    @pytest.mark.parametrize("aspect_scores", [BUSINESS_ONLY], indirect=True)
    @pytest.mark.parametrize(
        ("peak_share", "expected"),
        [(1.0, 95), (0.0, 0)],
        ids=["all of it", "none of it"],
    )
    def test_the_peak_share_moves_weight_to_the_worst_aspect(
        self,
        aspect_scores,
        peak_share,
        expected,
    ):
        assert overall(aspect_scores, peak_share) == expected

    @pytest.mark.parametrize("aspect_scores", [{"unverified": None}], indirect=True)
    def test_a_not_applicable_aspect_is_left_out(
        self,
        aspect_scores,
    ):
        assert overall(aspect_scores) == overall(aspect_scores.model_copy(update={"unverified": 20}))


class TestBands:
    """Scores map to bands, and a reviewer's levels to the score ranges of the bands, most severe first."""

    @pytest.mark.parametrize(
        ("level", "score_range", "name"),
        LEVEL_BANDS,
        ids=["level 0", "level 1", "level 2", "level 3"],
    )
    def test_a_level_takes_its_bands_range_and_the_static_score_places_inside_it(
        self,
        level,
        score_range,
        name,
    ):
        assert level_range(level) == score_range
        assert (place_in_level(level, 0), place_in_level(level, 100)) == score_range
        assert band(place_in_level(level, 55)) == name

    @pytest.mark.parametrize(
        ("name", "floor", "ceiling"),
        BAND_EDGES,
        ids=["Low", "Moderate", "High", "Critical"],
    )
    def test_a_band_runs_from_its_floor_to_its_ceiling(
        self,
        name,
        floor,
        ceiling,
    ):
        assert (band(floor), band(ceiling)) == (name, name)
        assert band_floor(name) == floor


class TestBusiness:
    """Business Impact: dependents, flows, a core-flow name and size; scaled down on tooling paths."""

    @pytest.mark.parametrize("facts", [{"fan_in": 6, "keyword": True}], indirect=True)
    def test_a_tooling_path_scales_the_score(
        self,
        facts,
        policy,
    ):
        plain, _ = business(facts, policy)
        tooling, evidence = business(facts.model_copy(update={"tooling": True}), policy)
        assert tooling == pytest.approx(plain * 0.5)
        assert evidence[0].startswith("Tooling path")

    @pytest.mark.parametrize("facts", [{"fan_in": 6, "keyword": True, "share": 0.25}], indirect=True)
    def test_without_flow_data_the_other_terms_carry_the_score(
        self,
        facts,
        policy,
    ):
        with_flows, _ = business(facts, policy)
        no_flows, evidence = business(facts.model_copy(update={"flows_available": False}), policy)
        assert with_flows == pytest.approx(55.0)  # dependents, keyword and size at full strength; no flows
        assert no_flows == pytest.approx(100.0)  # the same terms carry the whole weight
        assert any("no execution-flow data" in line for line in evidence)


class TestEffects:
    """Side Effects & Reversibility: write calls, migrations and dependents."""

    def test_writes_and_migrations_raise_the_score(
        self,
        facts,
        policy,
    ):
        quiet, _ = effects(facts, policy)
        busy, evidence = effects(
            facts.model_copy(
                update={
                    "effect_counts": Counter(db_write=10),
                    "effect_examples": {"db_write": "a.py:1"},
                    "migrations": 3,
                    "fan_in": 4,
                }
            ),
            policy,
        )
        assert busy > quiet + 40
        assert any("migration" in line for line in evidence)


class TestEntropy:
    """Entropy: the size and complexity of functions, exception handling, typing, coupling, big files and TODOs."""

    @pytest.mark.parametrize("facts", [{"funcs": [], "typed_share": None}], indirect=True)
    def test_without_functions_the_size_terms_are_neutral(
        self,
        facts,
        policy,
    ):
        score, evidence = entropy(facts, policy)
        assert any("No functions to measure" in line for line in evidence)
        assert 0 < score < 40


class TestUnverified:
    """Unverified: the functions linked to tests and the lines of those tests, plus a penalty for writes without an
    integration test; not applicable to manifests and test code."""

    @pytest.mark.parametrize(
        "facts",
        [{"graph_functions": 0, "linked_functions": 0, "linked_test_loc": 0.0, "test_scales": {}}],
        indirect=True,
    )
    def test_no_functions_scores_the_policys_neutral_value(
        self,
        facts,
        policy,
    ):
        score, _, info = unverified(facts, 0, policy)
        assert (score, info["coverage"]) == (policy.unverified_no_functions, None)

    @pytest.mark.parametrize("facts", [{"test_scales": {"unit": 3}}], indirect=True)
    def test_writes_without_an_integration_test_add_the_penalty(
        self,
        facts,
        policy,
    ):
        base, _, _ = unverified(facts, 10, policy)
        risky, _, _ = unverified(facts, 80, policy)
        assert base is not None
        assert risky == base + policy.no_integration_penalty

    @pytest.mark.parametrize(
        "facts",
        [{"linked_functions": 0, "linked_test_files": 0, "linked_test_loc": 0.0, "test_scales": {}}],
        indirect=True,
    )
    def test_untested_code_scores_at_the_top(
        self,
        facts,
        policy,
    ):
        untested, _, _ = unverified(facts, 0, policy)
        assert untested is not None
        assert untested >= 99

    @pytest.mark.parametrize("facts", [{"na_reason": "Infrastructure manifest"}], indirect=True)
    def test_a_not_applicable_module_is_not_rated(
        self,
        facts,
        policy,
    ):
        score, evidence, _ = unverified(facts, 90, policy)
        assert score is None
        assert evidence[0].startswith("Not applicable")


class TestRateModule:
    """Rating one module from its facts, and rescoring it from the stored facts under another policy."""

    @pytest.mark.parametrize(
        "facts",
        [{"effect_counts": {"db_write": 2}, "test_scales": {"unit": 1}, "funcs": [(70, 18), (5, 1)]}],
        indirect=True,
    )
    def test_stored_facts_reproduce_the_rating(
        self,
        facts,
        policy,
    ):
        rated = rate_module(facts, [], policy)
        assert rated.facts is not None
        stored = Facts.model_validate_json(rated.facts.model_dump_json())  # as data.json or a history keeps them
        assert stored == facts
        assert rescore(stored, policy) == rated.scores
        assert rated.baseline.model_dump() == rated.scores.model_dump(exclude={"overall"})
        louder = policy.model_copy(update={"effect_points": {**policy.effect_points, "db_write": 30}})
        assert rescore(stored, louder).effects > rated.scores.effects

    @pytest.mark.parametrize("facts", [{"fan_in": 12, "keyword": True, "share": 1.0}], indirect=True)
    @pytest.mark.parametrize(
        "policy",
        [{"business_weights": {"dependents": 0.9, "keyword": 0.9, "size": 0.9}}],
        indirect=True,
    )
    def test_scores_stay_in_range_when_tuned_weights_sum_past_one(
        self,
        facts,
        policy,
    ):
        assert rate_module(facts, [], policy).scores.biz == 100


class TestApplyLevels:
    """A reviewer's levels: each fixes an aspect's band, and the static score places the module inside it. A level
    that isn't one, a malformed judgement or a level for an aspect that doesn't apply changes nothing."""

    @pytest.mark.parametrize(
        ("judgement", "key", "level", "score_range"),
        EVERY_LEVEL,
        indirect=["judgement"],
        ids=EVERY_LEVEL_IDS,
    )
    def test_every_aspect_level_sets_its_band(
        self,
        heatmap,
        judgement,
        key,
        level,
        score_range,
    ):
        levelled = apply_levels(heatmap, judgement).modules[0]
        low, high = score_range
        assert low <= getattr(levelled.scores, key) <= high, getattr(levelled.scores, key)
        assert getattr(levelled.evidence, key)[0].startswith("Level %d" % level)

    @pytest.mark.parametrize(
        "judgement",
        [{"modules": {"m": {"business": {"level": 1, "reason": "core"}}}}],
        indirect=True,
    )
    def test_the_static_score_places_the_module_inside_the_band(
        self,
        heatmap,
        judgement,
    ):
        levelled = apply_levels(heatmap, judgement).modules[0]
        assert (levelled.scores.biz, band(levelled.scores.biz)) == (60, "High")

    @pytest.mark.parametrize(
        "judgement",
        INVALID_LEVEL_JUDGEMENTS,
        indirect=True,
        ids=[repr(level) for level in INVALID_LEVELS],
    )
    def test_an_invalid_level_is_ignored(
        self,
        heatmap,
        judgement,
    ):
        levelled = apply_levels(heatmap, judgement)
        assert levelled.modules[0].scores.biz == 40
        assert levelled.judged_modules == 0

    @pytest.mark.parametrize(
        "judgement",
        [[], "x", {"modules": []}, {"modules": {"m": "x"}}],
        indirect=True,
        ids=repr,
    )
    def test_a_malformed_judgement_is_ignored(
        self,
        heatmap,
        judgement,
    ):
        assert apply_levels(heatmap, judgement).judged_modules == 0

    @pytest.mark.parametrize("ratings", [[{"unverified": None}]], indirect=True)
    @pytest.mark.parametrize("judgement", [{"modules": {"m": {"unverified": {"level": 0}}}}], indirect=True)
    def test_a_level_for_a_not_applicable_aspect_is_ignored(
        self,
        heatmap,
        judgement,
    ):
        assert apply_levels(heatmap, judgement).modules[0].scores.unverified is None


class TestRefreshOverall:
    """After levelling, each module's overall score is recomputed from its aspect scores."""

    def test_overall_is_refreshed_with_the_policy_share(
        self,
        heatmap,
    ):
        heatmap.modules[0].scores.overall = 0
        assert refresh_overall(heatmap, 0.5).modules[0].scores.overall == overall(heatmap.modules[0].scores)


class TestMergeReadme:
    """The heat map's block goes between its markers in the README, else under the section's heading (replacing the
    section's body), else into a new section at the end."""

    def test_a_readme_without_the_section_gets_it_added(
        self,
    ):
        text, action = merge_readme(README, BLOCK)
        assert action == "ADD"
        assert text.startswith(README + "\n" + SECTION_HEADING + "\n\n")

    def test_the_block_between_the_markers_is_replaced(
        self,
    ):
        text, action = merge_readme(README_WITH_BLOCK, BLOCK)
        assert action == "UPDATE"
        assert "new" in text
        assert "old table" not in text
        assert text.endswith("\n\n## Next\nkeep\n")

    def test_without_a_readme_the_section_is_all_there_is(
        self,
    ):
        assert merge_readme(None, BLOCK)[0] == SECTION_HEADING + "\n\n" + BLOCK

    def test_a_heading_without_markers_gets_its_body_replaced(
        self,
    ):
        text, action = merge_readme(README_WITHOUT_MARKERS, BLOCK)
        assert action == "UPDATE"
        assert "stale" not in text
        assert text.count(SECTION_HEADING) == 1
        assert text.endswith("## Next\nkeep\n")


class TestFingerprint:
    """The fingerprint of what the diagram shows, and the one the README's block holds."""

    def test_the_commit_and_the_date_do_not_change_it(
        self,
        heatmap,
    ):
        later = heatmap.model_copy(update={"sha": "fff0000", "generated_at": "2030-01-01"})
        assert fingerprint(later) == fingerprint(heatmap)

    def test_a_score_changes_it(
        self,
        heatmap,
    ):
        rescored = heatmap.model_copy(deep=True)
        rescored.modules[0].scores.biz = 41
        assert fingerprint(rescored) != fingerprint(heatmap)

    @pytest.mark.parametrize(
        ("readme", "expected"),
        [("x\n" + BLOCK, "0123456789ab"), ("no block", None)],
        ids=["a block", "no block"],
    )
    def test_the_readme_block_gives_its_fingerprint_back(
        self,
        readme,
        expected,
    ):
        assert block_fingerprint(readme) == expected
