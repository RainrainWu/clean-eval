"""The presenters (adaptor/presenters.py): the treemap layout, the README diagram and block, the Markdown summary,
and the review comment."""

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


class TestReviewComment:
    """A review's comment, in the one layout every review comment has: the one the pr-review skill's template shows, so
    the interactive review writes what CI renders. Its tables keep a row to a line, whatever the findings' text holds;
    an empty section says so, names are escaped and the modules cut."""

    def test_the_template_shows_what_the_presenter_renders(
        self,
        review_comment,
        comment_template,
    ):
        assert "```markdown\n" + MarkdownReportPresenter().comment(review_comment) + "```" in comment_template

    @pytest.mark.parametrize(
        "review_comment",
        [{"verdict": "approve", "max_tier": "Low", "max_score": 3, "violations": [], "uncovered": [], "notes": ""}],
        indirect=True,
    )
    def test_empty_sections_say_so(
        self,
        review_comment,
    ):
        text = MarkdownReportPresenter().comment(review_comment)
        assert text.startswith("## clean-eval review: Approve\n")
        assert "| 0 blocker · 0 major · 0 minor |" in text
        assert text.count("None.") == 2
        assert text.endswith("### Notes\n\n_No notes._\n")

    @pytest.mark.parametrize(
        "review_comment",
        [{"modules": [{"module": "src/<x>&y|z`%d" % n, "band": "Low", "score": n, "rated": True} for n in range(7)]}],
        indirect=True,
    )
    def test_module_names_are_escaped_and_the_riskiest_five_named(
        self,
        review_comment,
    ):
        summary = MarkdownReportPresenter().comment(review_comment).splitlines()[4]  # the summary table's row
        assert summary.count("`src/<x>&y\\|z'") == 5
        assert " and 2 more |" in summary

    @pytest.mark.parametrize(
        "review_comment",
        [{"uncovered": [{"rule": "NEW", "severity": "major", "summary": "a | b\nc", "fix": ""}]}],
        indirect=True,
    )
    def test_a_findings_text_stays_in_its_cell(
        self,
        review_comment,
    ):
        assert "| major | — | a \\| b c | — |" in MarkdownReportPresenter().comment(review_comment).splitlines()
