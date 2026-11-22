"""The presenters (adaptor/presenters.py): the treemap layout, the README diagram and block, and the Markdown
summary."""

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.adaptor.presenters import (
    MarkdownReportPresenter,
    SvgDiagramPresenter,
    squarify,
)
from clean_eval.use_cases.rules.heatmap import MARKERS


class TestSquarify:
    """The squarified treemap layout: its tiles fill the canvas."""

    def test_squarify_fills_the_canvas_without_loss(
        self,
    ):
        rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
        assert len(rects) == 3
        assert sum(width * height for payload, left, top, width, height in rects) == pytest.approx(10000.0)


@pytest.mark.parametrize("ratings", [lf("mixed_ratings")], indirect=True, ids=["mixed"])
class TestSvgDiagramPresenter:
    """The README diagram: the SVG treemap, one tile per module with its name escaped, and the README block around
    it."""

    def test_svg_has_one_tile_per_module_and_escapes_names(
        self,
        heatmap,
    ):
        svg = SvgDiagramPresenter().svg(heatmap)
        assert svg.count("<rect ") == 4  # three tiles and the backing
        assert "src/&lt;x&gt;&amp;y|z" in svg
        assert "<x>" not in svg
        assert svg.count('fill-opacity="0.35"') == 3
        assert 'fill="#0d1117"' in svg

    def test_readme_block_has_markers_fingerprint_image_and_legend(
        self,
        heatmap,
    ):
        block = SvgDiagramPresenter().readme_block(heatmap, "docs/risk-heatmap.svg", "0123456789ab")
        assert block.startswith(MARKERS[0])
        assert block.rstrip().endswith(MARKERS[1])
        assert "<!-- risk-heat-map:fingerprint=0123456789ab -->" in block
        assert "![Risk heat map of demo at abc1234](docs/risk-heatmap.svg)" in block
        assert "🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100" in block
        assert "## " not in block  # the section heading lives outside the markers


class TestMarkdownReportPresenter:
    """The Markdown summary: the modules ranked by overall score with their names escaped, or a note that there are
    none."""

    @pytest.mark.parametrize("ratings", [lf("mixed_ratings")], indirect=True, ids=["mixed"])
    def test_modules_are_ranked_and_their_names_escaped(
        self,
        heatmap,
    ):
        text = MarkdownReportPresenter().report(heatmap)
        rows = [line for line in text.splitlines() if line.startswith("| `")]
        assert rows[0].startswith("| `src/api`")
        assert "`src/<x>&y\\|z`" in text
        assert "⚪ N/A" in text

    @pytest.mark.parametrize("ratings", [[]], indirect=True, ids=["no modules"])
    def test_a_heat_map_without_modules(
        self,
        heatmap,
    ):
        assert "No Python source" in MarkdownReportPresenter().report(heatmap)
