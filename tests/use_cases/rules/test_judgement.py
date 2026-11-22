"""Applying a reviewer's levels (use_cases/rules/judgement.py): each level places a score within its band."""
import unittest

from tests.domain.builders import heatmap, rating

from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import ASPECTS
from clean_eval.use_cases.rules.judgement import apply_levels, refresh_overall
from clean_eval.use_cases.rules.scoring import band, overall

def levels(raw):
    return Judgement.model_validate(raw)


class JudgementRule(unittest.TestCase):
    def test_every_aspect_level_sets_its_band_and_the_static_score_places_inside_it(self):
        for name, key, *_ in ASPECTS:
            for level, (lo, hi) in enumerate(((75, 100), (50, 74), (25, 49), (0, 24))):
                h = apply_levels(heatmap(rating()), levels({"modules": {"m": {name: {"level": level, "reason": "r"}}}}))
                m = h.modules[0]
                self.assertTrue(lo <= getattr(m.scores, key) <= hi, (name, level, getattr(m.scores, key)))
                self.assertTrue(getattr(m.evidence, key)[0].startswith("Level %d" % level))
        m = apply_levels(heatmap(rating()), levels({"modules": {"m": {"business": {"level": 1, "reason": "core"}}}})).modules[0]
        self.assertEqual((m.scores.biz, band(m.scores.biz)), (60, "High"))

    def test_invalid_judgements_are_ignored(self):
        for bad in ("high", 4, -1, 1.5, True, None, [1], {"a": 1}):
            h = apply_levels(heatmap(rating()), levels({"modules": {"m": {"business": {"level": bad}},
                                                                    "nope": {"business": {"level": 0}}}}))
            self.assertEqual(h.modules[0].scores.biz, 40, bad)
            self.assertEqual(h.judged_modules, 0)
        for bad in ([], "x", {"modules": []}, {"modules": {"m": "x"}}):
            self.assertEqual(apply_levels(heatmap(rating()), levels(bad)).judged_modules, 0)
        h = apply_levels(heatmap(rating(unverified=None)), levels({"modules": {"m": {"unverified": {"level": 0}}}}))
        self.assertIsNone(h.modules[0].scores.unverified)

    def test_overall_is_refreshed_with_the_policy_share(self):
        h = heatmap(rating())
        h.modules[0].scores.overall = 0
        self.assertEqual(refresh_overall(h, 0.5).modules[0].scores.overall, overall(h.modules[0].scores))


if __name__ == "__main__":
    unittest.main()
