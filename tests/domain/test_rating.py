"""The rating models (domain/rating.py): score ranges, facts that survive JSON, assignments that are validated."""
import unittest

from pydantic import ValidationError

from tests.domain.builders import facts, rating

from clean_eval.domain.change import ModuleRecord
from clean_eval.domain.rating import Facts, Scores
from clean_eval.domain.values import BAND_NAMES, Band


class RatingModels(unittest.TestCase):
    def test_scores_are_0_to_100_and_only_unverified_may_be_na(self):
        Scores(biz=0, effects=100, entropy=5, unverified=None, overall=60)
        for bad in ({"biz": 101}, {"entropy": -1}, {"biz": None}, {"overall": "high"}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                Scores(**{"biz": 1, "effects": 1, "entropy": 1, "unverified": 1, "overall": 1, **bad})

    def test_assignments_are_validated(self):
        m = rating()
        with self.assertRaises(ValidationError):
            m.scores.biz = 140
        with self.assertRaises(ValidationError):
            m.loc = -5

    def test_facts_keep_their_counters_through_json(self):
        f = facts()
        back = Facts.model_validate_json(f.model_dump_json())
        self.assertEqual(back, f)
        self.assertEqual(back.test_scales.most_common(1), [("unit", 2)])
        with self.assertRaises(ValidationError):
            facts(share=1.5)

    def test_rated_means_scored(self):
        ModuleRecord(module="m", band="Low", score=10, rated=True)
        ModuleRecord(module="infra", band="Moderate", rated=False)
        for bad in ({"rated": True}, {"rated": False, "score": 30}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                ModuleRecord(module="m", band="Low", **bad)

    def test_the_band_type_matches_the_bands(self):
        self.assertEqual(Band.__args__, BAND_NAMES)


if __name__ == "__main__":
    unittest.main()
