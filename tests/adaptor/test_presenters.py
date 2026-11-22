"""The presenters (adaptor/presenters.py): the treemap layout, the README diagram and block, and the Markdown
summary."""

import pytest

from clean_eval.adaptor.presenters import MarkdownReportPresenter, SvgDiagramPresenter, squarify
from clean_eval.use_cases.rules.heatmap import MARKERS
from tests.domain.builders import heatmap, rating

# Tests of clean_eval/adaptor/presenters.


# The squarified treemap layout (adaptor/presenters.py).


def test_squarify_fills_the_canvas_without_loss():
    rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
    assert len(rects) == 3
    assert sum(w * h for _, _, _, w, h in rects) == pytest.approx(10000.0)


# The README diagram presenter (adaptor/presenters.py): the SVG treemap and the README block around it.


def mixed():
    return heatmap(
        rating("src/api", 3000, 90, 80, 30, 70),
        rating("src/<x>&y|z", 500, 10, 5, 5, 20),
        rating("terraform/lambda", 700, 20, 20, 10, None),
    )


def test_svg_has_one_tile_per_module_and_escapes_names():
    svg = SvgDiagramPresenter().svg(mixed())
    assert svg.count("<rect ") == 4  # three tiles and the backing
    assert "src/&lt;x&gt;&amp;y|z" in svg
    assert "<x>" not in svg
    assert svg.count('fill-opacity="0.35"') == 3
    assert 'fill="#0d1117"' in svg


def test_readme_block_has_markers_fingerprint_image_and_legend():
    block = SvgDiagramPresenter().readme_block(mixed(), "docs/risk-heatmap.svg", "0123456789ab")
    assert block.startswith(MARKERS[0])
    assert block.rstrip().endswith(MARKERS[1])
    assert "<!-- risk-heat-map:fingerprint=0123456789ab -->" in block
    assert "![Risk heat map of demo at abc1234](docs/risk-heatmap.svg)" in block
    assert "🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100" in block
    assert "## " not in block  # the section heading lives outside the markers


# The Markdown summary presenter (adaptor/presenters.py).


def test_modules_are_ranked_and_their_names_escaped():
    text = MarkdownReportPresenter().report(
        heatmap(
            rating("src/api", 3000, 90, 80, 30, 70),
            rating("src/<x>&y|z", 500, 10, 5, 5, 20),
            rating("terraform/lambda", 700, 20, 20, 10, None),
        )
    )
    rows = [line for line in text.splitlines() if line.startswith("| `")]
    assert rows[0].startswith("| `src/api`")
    assert "`src/<x>&y\\|z`" in text
    assert "⚪ N/A" in text


def test_a_heat_map_without_modules():
    empty = heatmap()
    empty.modules = []
    assert "No Python source" in MarkdownReportPresenter().report(empty)
