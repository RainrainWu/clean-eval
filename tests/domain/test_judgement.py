"""A reviewer's judgement (domain/judgement.py): levels that don't validate are dropped, not raised."""
import unittest

from clean_eval.domain.judgement import Judgement


class JudgementModel(unittest.TestCase):
    def test_invalid_entries_are_dropped_not_fatal(self):
        j = Judgement.model_validate({"modules": {
            "a": {"business": {"level": 1, "reason": "core path " * 50}, "effects": {"level": 9}, "nope": {"level": 0}},
            "b": {"entropy": {"level": True}}, "c": "x", 7: {"business": {"level": 0}}}})
        self.assertEqual(list(j.modules), ["a"])
        self.assertEqual(list(j.modules["a"]), ["business"])
        self.assertEqual(len(j.modules["a"]["business"].reason), 240)
        self.assertEqual(Judgement.model_validate("not an object").modules, {})


if __name__ == "__main__":
    unittest.main()
