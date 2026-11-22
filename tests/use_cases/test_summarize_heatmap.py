"""Summarizing a heat map (use_cases/summarize_heatmap.py and finalize_heatmap.py): levels applied, then a
ReportPresenter's report."""
import unittest

from tests.domain.builders import heatmap, rating
from tests.use_cases.fakes import fake

from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import Policy
from clean_eval.use_cases.finalize_heatmap import finalize_heatmap
from clean_eval.use_cases.interfaces import ReportPresenter
from clean_eval.use_cases.summarize_heatmap import summarize_heatmap

P = Policy()


class SummarizeHeatmapTest(unittest.TestCase):
    def test_levels_are_applied_before_summarizing(self):
        report = fake(ReportPresenter, report=lambda heatmap: "%d judged" % heatmap.judged_modules)
        judged = Judgement.model_validate({"modules": {"m": {"business": {"level": 0, "reason": "r"}}}})
        final, text = summarize_heatmap(report, heatmap(rating()), judged, P)
        self.assertEqual((text, final.judged_modules), ("1 judged", 1))
        self.assertGreaterEqual(final.modules[0].scores.biz, 75)
        self.assertEqual(finalize_heatmap(heatmap(rating()), None, P).judged_modules, 0)


if __name__ == "__main__":
    unittest.main()
