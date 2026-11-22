"""The eval's tuning models (domain/tuning.py): proposed policy and rules edits, and their outcome."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.tuning import AgentEvalResult, EvalResult, ModelChange, PolicyEdit, Rejection, RulesChange


class TestTuningModels:
    def test_policy_edits(self):
        edit = PolicyEdit(new={"business_weights": {"dependents": 0.45}, "_why": "x"}, orig={"_why": "y"})
        assert edit.changed_keys == {"business_weights"}

    def test_recommendations_are_text(self):
        r = AgentEvalResult.model_validate(
            {
                "recommendations": [{"target": 1, "change": None, "evidence": "e" * 3000}, "junk"]
                + [{"target": "t"}] * 40,
                "auc": 0.99,
            }
        )
        assert len(r.recommendations) == 30
        first = r.recommendations[0]
        assert (first.target, first.change, len(first.evidence)) == ("1", "None", 2000)

    def test_applied_is_derived(self):
        base = dict(since="2026-01-01", until="2026-02-01", prs=30, defective_prs=6, auc=0.7)
        assert not EvalResult(**base, model_change=Rejection(reason="no")).applied
        assert EvalResult(**base, rules_change=RulesChange()).applied
        accepted = EvalResult(**base, model_change=ModelChange(changed_keys=["x"], auc_before=0.6, auc_after=0.7))
        assert EvalResult.model_validate(accepted.model_dump()).model_change == accepted.model_change


class TestModelChange:
    def test_tunable_change_ok(self):
        new = {"business_weights": {"dependents": 0.45}, "unrated_band": "High", "_why": "a comment"}
        assert PolicyEdit(new=new).changed_keys == {"business_weights", "unrated_band"}
        assert PolicyEdit(new={"version": 3}, orig={"version": 2}).changed_keys == {"version"}

    @pytest.mark.parametrize(
        "new",
        [
            {"depth": 1},
            {"exclude": ["**"]},
            {"tooling": "x"},
            {"business_weights": {"dependents": -1}},
            {"business_weights": {"unknown": 1}},
            {"unrated_band": "Huge"},
            {"overall_peak_share": 2},
            {"window_days": 30},
            {"version": "2"},
            {"effects_scale": 0},
            {"business_saturation": {"size": 0}},
            [1],
        ],
        ids=repr,
    )
    def test_structural_or_bad_changes_rejected(self, new):
        with pytest.raises(ValidationError):
            PolicyEdit(new=new)
