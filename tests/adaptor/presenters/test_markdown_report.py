"""The Markdown summary presenter (adaptor/presenters/markdown_report.py)."""
import unittest

from tests.domain.builders import heatmap, rating

from clean_eval.adaptor.presenters.markdown_report import MarkdownReportPresenter


class MarkdownReportPresenterTest(unittest.TestCase):
    def test_modules_are_ranked_and_their_names_escaped(self):
        text = MarkdownReportPresenter().report(heatmap(
            rating("src/api", 3000, 90, 80, 30, 70), rating("src/<x>&y|z", 500, 10, 5, 5, 20),
            rating("terraform/lambda", 700, 20, 20, 10, None)))
        rows = [line for line in text.splitlines() if line.startswith("| `")]
        self.assertTrue(rows[0].startswith("| `src/api`"))
        self.assertIn("`src/<x>&y\\|z`", text)
        self.assertIn("⚪ N/A", text)

    def test_a_heat_map_without_modules(self):
        empty = heatmap()
        empty.modules = []
        self.assertIn("No Python source", MarkdownReportPresenter().report(empty))


if __name__ == "__main__":
    unittest.main()
