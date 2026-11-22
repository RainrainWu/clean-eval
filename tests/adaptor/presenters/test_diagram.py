"""The README diagram presenter (adaptor/presenters/diagram.py): the SVG treemap and the README block around it."""
import unittest

from tests.domain.builders import heatmap, rating

from clean_eval.adaptor.presenters.diagram import SvgDiagramPresenter
from clean_eval.use_cases.rules.readme import MARKERS


def mixed():
    return heatmap(rating("src/api", 3000, 90, 80, 30, 70), rating("src/<x>&y|z", 500, 10, 5, 5, 20),
                   rating("terraform/lambda", 700, 20, 20, 10, None))


class SvgDiagramPresenterTest(unittest.TestCase):
    def test_svg_has_one_tile_per_module_and_escapes_names(self):
        svg = SvgDiagramPresenter().svg(mixed())
        self.assertEqual(svg.count("<rect "), 4)  # three tiles and the backing
        self.assertIn("src/&lt;x&gt;&amp;y|z", svg)
        self.assertNotIn("<x>", svg)
        self.assertEqual(svg.count('fill-opacity="0.35"'), 3)
        self.assertIn('fill="#0d1117"', svg)

    def test_readme_block_has_markers_fingerprint_image_and_legend(self):
        block = SvgDiagramPresenter().readme_block(mixed(), "docs/risk-heatmap.svg", "0123456789ab")
        self.assertTrue(block.startswith(MARKERS[0]) and block.rstrip().endswith(MARKERS[1]))
        self.assertIn("<!-- risk-heat-map:fingerprint=0123456789ab -->", block)
        self.assertIn("![Risk heat map of demo at abc1234](docs/risk-heatmap.svg)", block)
        self.assertIn("🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100", block)
        self.assertNotIn("## ", block)  # the section heading lives outside the markers


if __name__ == "__main__":
    unittest.main()
