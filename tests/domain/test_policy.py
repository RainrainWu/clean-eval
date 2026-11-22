"""The risk policy (domain/policy.py): overrides merge into the defaults and are validated."""
import unittest

from pydantic import ValidationError

from clean_eval.domain.errors import InvalidPolicy
from clean_eval.domain.policy import STRUCTURAL, TUNABLE, Policy

P = Policy()


class PolicyTest(unittest.TestCase):
    def test_overrides_merge_and_are_checked(self):
        p = Policy.from_overrides({"business_weights": {"dependents": 0.4}, "unrated_band": "High", "_note": "comment"})
        self.assertEqual(p.business_weights["dependents"], 0.4)
        self.assertEqual(p.business_weights["size"], P.business_weights["size"])  # other keys keep their defaults
        self.assertEqual(p.unrated_band, "High")
        self.assertEqual(Policy.from_overrides({}), P)
        self.assertEqual(Policy.from_overrides(None), P)

    def test_divisors_must_stay_above_zero(self):
        for bad in ({"effects_scale": 0}, {"effects_dependents_saturation": 0}, {"business_saturation": {"dependents": 0}},
                    {"entropy_saturation": {"todo_per_k": 0}}, {"longest_range": 0}, {"avg_complexity_range": 0},
                    {"unverified_ratio_target": 0}):
            with self.assertRaises(InvalidPolicy, msg=repr(bad)) as cm:
                Policy.from_overrides(bad)
            self.assertIn("greater than 0", str(cm.exception))
        self.assertEqual(Policy.from_overrides({"business_weights": {"keyword": 0}}).business_weights["keyword"], 0)  # weights may be 0

    def test_invalid_overrides_are_refused(self):
        for bad in ({"nope": 1}, {"business_weights": {"unknown": 1}}, {"business_weights": {"dependents": -1}},
                    {"business_weights": {"dependents": True}}, {"unrated_band": "Huge"}, {"overall_peak_share": 2},
                    {"depth": 0}, {"depth": 1.5}, {"tooling": "("}, {"exclude": "vendor/*"}, {"not_applicable": [["x"]]},
                    {"not_applicable": [["reason", "("]]}, {"business_weights": {k: 0 for k in P.business_weights}},
                    {"version": "2"}, {"effects_scale": "40"}, {"effects_scale": float("nan")},
                    {"business_weights": {"size": float("inf")}}, [1]):
            with self.assertRaises(InvalidPolicy, msg=repr(bad)):
                Policy.from_overrides(bad)

    def test_messages_say_where_and_what(self):
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_overrides({"nope": 1, "depth": 0}, "risk-model.json")
        self.assertEqual(str(cm.exception), "risk-model.json: depth: Input should be greater than or equal to 1; nope: unknown key")
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_text("{not json", "risk-model.json")
        self.assertIn("risk-model.json is not valid JSON", str(cm.exception))

    def test_v1_model_files_get_a_migration_hint(self):
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_overrides({"window_days": 30, "likelihood_weights": {}})
        self.assertIn("v1 model", str(cm.exception))

    def test_tunable_and_structural_knobs_partition_the_policy(self):
        self.assertFalse(TUNABLE & STRUCTURAL)
        self.assertIn("business_weights", TUNABLE)
        self.assertIn("depth", STRUCTURAL)
        self.assertNotIn("version", TUNABLE)

    def test_the_policy_is_immutable(self):
        with self.assertRaises(ValidationError):
            P.depth = 1


if __name__ == "__main__":
    unittest.main()
