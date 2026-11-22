"""The risk policy (domain/policy.py): overrides merge into the defaults and are validated, the knobs split into
tunable and structural ones, and the band type names the bands."""

import pytest
from pydantic import ValidationError
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from clean_eval.domain.errors import InvalidPolicy
from clean_eval.domain.policy import (
    BANDS,
    STRUCTURAL,
    TUNABLE,
    Band,
    Policy,
)


class TestFromOverrides:
    """Overrides merge into the defaults: a dictionary knob keeps the defaults of the keys it doesn't name, and comments
    are dropped."""

    @pytest.mark.parametrize(
        "policy",
        [{"business_weights": {"dependents": 0.4}, "unrated_band": "High", "_note": "comment"}],
        indirect=True,
        ids=["a weight, a band and a comment"],
    )
    def test_overrides_merge_into_the_defaults(
        self,
        policy,
        default_policy,
    ):
        assert policy.business_weights["dependents"] == 0.4
        assert policy.business_weights["size"] == default_policy.business_weights["size"]  # other keys keep defaults
        assert policy.unrated_band == "High"

    @pytest.mark.parametrize("policy", [{"business_weights": {"keyword": 0}}], indirect=True, ids=["keyword 0"])
    def test_weights_may_be_zero(
        self,
        policy,
    ):
        assert policy.business_weights["keyword"] == 0


class TestRefusedOverrides:
    """Overrides the validators refuse raise InvalidPolicy, which says where and what."""

    @pytest.mark.parametrize(
        "overrides",
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
    def test_divisors_must_stay_above_zero(
        self,
        overrides,
    ):
        with pytest.raises(InvalidPolicy) as raised:
            Policy.from_overrides(overrides)
        assert "greater than 0" in str(raised.value)

    @pytest.mark.parametrize(
        "overrides",
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
            pytest.param(
                lfc(
                    lambda defaults: {"business_weights": dict.fromkeys(defaults.business_weights, 0)},
                    lf("default_policy"),
                ),
                id="{'business_weights': every default at 0}",
            ),
            {"version": "2"},
            {"effects_scale": "40"},
            {"effects_scale": float("nan")},
            {"business_weights": {"size": float("inf")}},
            [1],
        ],
        ids=repr,
    )
    def test_invalid_overrides_are_refused(
        self,
        overrides,
    ):
        with pytest.raises(InvalidPolicy):
            Policy.from_overrides(overrides)

    def test_the_message_says_where_and_what(
        self,
    ):
        with pytest.raises(InvalidPolicy) as raised:
            Policy.from_overrides({"nope": 1, "depth": 0}, "risk-model.json")
        message = str(raised.value)
        assert message == "risk-model.json: depth: Input should be greater than or equal to 1; nope: unknown key"

    def test_v1_model_files_get_a_migration_hint(
        self,
    ):
        with pytest.raises(InvalidPolicy) as raised:
            Policy.from_overrides({"window_days": 30, "likelihood_weights": {}})
        assert "v1 model" in str(raised.value)


class TestFromText:
    """The policy a risk-model.json's text describes: text that isn't JSON is refused, naming the file."""

    def test_text_that_is_not_json_is_refused_naming_the_file(
        self,
    ):
        with pytest.raises(InvalidPolicy) as raised:
            Policy.from_text("{not json", "risk-model.json")
        assert "risk-model.json is not valid JSON" in str(raised.value)


class TestPolicy:
    """The policy, once read, is frozen."""

    def test_the_policy_is_immutable(
        self,
        policy,
    ):
        with pytest.raises(ValidationError):
            policy.depth = 1


class TestKnobs:
    """The knobs a calibration may tune and the structural ones, which decide what is measured, split the policy."""

    def test_tunable_and_structural_knobs_partition_the_policy(
        self,
    ):
        assert not TUNABLE & STRUCTURAL
        assert "business_weights" in TUNABLE
        assert "depth" in STRUCTURAL
        assert "version" not in TUNABLE


class TestBands:
    """The score bands and the bands they bound."""

    def test_the_bands_are_bounded_in_their_order(
        self,
    ):
        assert [band for _, band in BANDS] == list(Band)
        assert [band.rank for band in Band] == [0, 1, 2, 3]
