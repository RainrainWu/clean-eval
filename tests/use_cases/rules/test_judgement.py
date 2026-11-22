"""Applying a reviewer's levels (use_cases/rules/judgement.py): each level places a score within its band."""

import pytest

from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import ASPECTS
from clean_eval.use_cases.rules.judgement import apply_levels, refresh_overall
from clean_eval.use_cases.rules.scoring import band, overall
from tests.domain.builders import heatmap, rating


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
