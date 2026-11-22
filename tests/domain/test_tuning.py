"""The eval's tuning models (domain/tuning.py): proposed policy and rules edits, and their outcome."""
import unittest

from pydantic import ValidationError

from clean_eval.domain.tuning import AgentEvalResult, EvalResult, ModelChange, PolicyEdit, Rejection, RulesChange


class TuningModels(unittest.TestCase):
    def test_policy_edits(self):
        edit = PolicyEdit(new={"business_weights": {"dependents": 0.45}, "_why": "x"}, orig={"_why": "y"})
        self.assertEqual(edit.changed_keys, {"business_weights"})
        for new in ({"depth": 1}, {"business_weights": {"dependents": -1}}):
            with self.assertRaises(ValidationError, msg=repr(new)):
                PolicyEdit(new=new)

    def test_recommendations_are_text(self):
        r = AgentEvalResult.model_validate({"recommendations": [{"target": 1, "change": None, "evidence": "e" * 3000}, "junk"]
                                            + [{"target": "t"}] * 40, "auc": 0.99})
        self.assertEqual(len(r.recommendations), 30)
        first = r.recommendations[0]
        self.assertEqual((first.target, first.change, len(first.evidence)), ("1", "None", 2000))

    def test_applied_is_derived(self):
        base = dict(since="2026-01-01", until="2026-02-01", prs=30, defective_prs=6, auc=0.7)
        self.assertFalse(EvalResult(**base, model_change=Rejection(reason="no")).applied)
        self.assertTrue(EvalResult(**base, rules_change=RulesChange()).applied)
        accepted = EvalResult(**base, model_change=ModelChange(changed_keys=["x"], auc_before=0.6, auc_after=0.7))
        self.assertEqual(EvalResult.model_validate(accepted.model_dump()).model_change, accepted.model_change)


class ModelChangeTest(unittest.TestCase):
    def test_tunable_change_ok(self):
        new = {"business_weights": {"dependents": 0.45}, "unrated_band": "High", "_why": "a comment"}
        self.assertEqual(PolicyEdit(new=new).changed_keys, {"business_weights", "unrated_band"})
        self.assertEqual(PolicyEdit(new={"version": 3}, orig={"version": 2}).changed_keys, {"version"})

    def test_structural_or_bad_changes_rejected(self):
        for new in ({"depth": 1}, {"exclude": ["**"]}, {"tooling": "x"}, {"business_weights": {"dependents": -1}},
                    {"business_weights": {"unknown": 1}}, {"unrated_band": "Huge"}, {"overall_peak_share": 2},
                    {"window_days": 30}, {"version": "2"}, {"effects_scale": 0}, {"business_saturation": {"size": 0}}, [1]):
            with self.assertRaises(ValidationError, msg=repr(new)):
                PolicyEdit(new=new)


if __name__ == "__main__":
    unittest.main()
