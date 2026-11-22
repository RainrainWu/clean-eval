"""The Markdown summary presenter (adaptor/presenters/markdown_report.py)."""

from clean_eval.adaptor.presenters.markdown_report import MarkdownReportPresenter
from tests.domain.builders import heatmap, rating


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
