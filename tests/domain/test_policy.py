"""The risk policy (domain/policy.py): overrides merge into the defaults and are validated."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.errors import InvalidPolicy
from clean_eval.domain.policy import STRUCTURAL, TUNABLE, Policy

P = Policy()


def test_overrides_merge_and_are_checked():
    p = Policy.from_overrides({"business_weights": {"dependents": 0.4}, "unrated_band": "High", "_note": "comment"})
    assert p.business_weights["dependents"] == 0.4
    assert p.business_weights["size"] == P.business_weights["size"]  # other keys keep their defaults
    assert p.unrated_band == "High"
    assert Policy.from_overrides({}) == P
    assert Policy.from_overrides(None) == P


@pytest.mark.parametrize(
    "bad",
    [
        {"effects_scale": 0},
        {"effects_dependents_saturation": 0},
        {"business_saturation": {"dependents": 0}},
        {"entropy_saturation": {"todo_per_k": 0}},
        {"longest_range": 0},
        {"avg_complexity_range": 0},
        {"unverified_ratio_target": 0},
    ],
    ids=repr,
)
def test_divisors_must_stay_above_zero(bad):
    with pytest.raises(InvalidPolicy) as cm:
        Policy.from_overrides(bad)
    assert "greater than 0" in str(cm.value)


def test_weights_may_be_zero():
    assert (
        Policy.from_overrides({"business_weights": {"keyword": 0}}).business_weights["keyword"] == 0
    )  # weights may be 0


@pytest.mark.parametrize(
    "bad",
    [
        {"nope": 1},
        {"business_weights": {"unknown": 1}},
        {"business_weights": {"dependents": -1}},
        {"business_weights": {"dependents": True}},
        {"unrated_band": "Huge"},
        {"overall_peak_share": 2},
        {"depth": 0},
        {"depth": 1.5},
        {"tooling": "("},
        {"exclude": "vendor/*"},
        {"not_applicable": [["x"]]},
        {"not_applicable": [["reason", "("]]},
        {"business_weights": {k: 0 for k in P.business_weights}},
        {"version": "2"},
        {"effects_scale": "40"},
        {"effects_scale": float("nan")},
        {"business_weights": {"size": float("inf")}},
        [1],
    ],
    ids=repr,
)
def test_invalid_overrides_are_refused(bad):
    with pytest.raises(InvalidPolicy):
        Policy.from_overrides(bad)


def test_messages_say_where_and_what():
    with pytest.raises(InvalidPolicy) as cm:
        Policy.from_overrides({"nope": 1, "depth": 0}, "risk-model.json")
    assert str(cm.value) == "risk-model.json: depth: Input should be greater than or equal to 1; nope: unknown key"
    with pytest.raises(InvalidPolicy) as cm:
        Policy.from_text("{not json", "risk-model.json")
    assert "risk-model.json is not valid JSON" in str(cm.value)


def test_v1_model_files_get_a_migration_hint():
    with pytest.raises(InvalidPolicy) as cm:
        Policy.from_overrides({"window_days": 30, "likelihood_weights": {}})
    assert "v1 model" in str(cm.value)


def test_tunable_and_structural_knobs_partition_the_policy():
    assert not TUNABLE & STRUCTURAL
    assert "business_weights" in TUNABLE
    assert "depth" in STRUCTURAL
    assert "version" not in TUNABLE


def test_the_policy_is_immutable():
    with pytest.raises(ValidationError):
        P.depth = 1
