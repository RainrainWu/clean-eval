"""The adaptor layer: the presenters that render a heat map (they implement the use cases' DiagramPresenter and
ReportPresenter)."""
import unittest

from helpers import heatmap, rating

from clean_eval.adaptor.presenters.diagram import SvgDiagramPresenter
from clean_eval.adaptor.presenters.markdown_report import MarkdownReportPresenter
from clean_eval.adaptor.presenters.treemap import squarify
from clean_eval.use_cases.rules.readme import MARKERS


class Presenters(unittest.TestCase):
    def mixed(self):
        return heatmap(rating("src/api", 3000, 90, 80, 30, 70), rating("src/<x>&y|z", 500, 10, 5, 5, 20),
                       rating("terraform/lambda", 700, 20, 20, 10, None))

    def test_squarify_fills_the_canvas_without_loss(self):
        rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
        self.assertEqual(len(rects), 3)
        self.assertAlmostEqual(sum(w * h for _, _, _, w, h in rects), 10000.0)

    def test_svg_has_one_tile_per_module_and_escapes_names(self):
        svg = SvgDiagramPresenter().svg(self.mixed())
        self.assertEqual(svg.count("<rect "), 4)  # three tiles and the backing
        self.assertIn("src/&lt;x&gt;&amp;y|z", svg)
        self.assertNotIn("<x>", svg)
        self.assertEqual(svg.count('fill-opacity="0.35"'), 3)
        self.assertIn('fill="#0d1117"', svg)

    def test_readme_block_has_markers_fingerprint_image_and_legend(self):
        block = SvgDiagramPresenter().readme_block(self.mixed(), "docs/risk-heatmap.svg", "0123456789ab")
        self.assertTrue(block.startswith(MARKERS[0]) and block.rstrip().endswith(MARKERS[1]))
        self.assertIn("<!-- risk-heat-map:fingerprint=0123456789ab -->", block)
        self.assertIn("![Risk heat map of demo at abc1234](docs/risk-heatmap.svg)", block)
        self.assertIn("🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100", block)
        self.assertNotIn("## ", block)  # the section heading lives outside the markers

    def test_markdown_report_ranks_and_escapes(self):
        text = MarkdownReportPresenter().report(self.mixed())
        rows = [l for l in text.splitlines() if l.startswith("| `")]
        self.assertTrue(rows[0].startswith("| `src/api`"))
        self.assertIn("`src/<x>&y\\|z`", text)
        self.assertIn("⚪ N/A", text)
        empty = heatmap()
        empty.modules = []
        self.assertIn("No Python source", MarkdownReportPresenter().report(empty))


if __name__ == "__main__":
    unittest.main()
