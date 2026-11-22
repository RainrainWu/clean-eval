"""Reading the merged-PR history (use_cases/rules/history.py): PR numbers and rule tags in commit and review text."""
import unittest

from clean_eval.use_cases.rules import history


class ParsingTest(unittest.TestCase):
    def test_pr_number(self):
        self.assertEqual(history.pr_number("Fix #45 (#67)"), 67)
        self.assertEqual(history.pr_number("Merge pull request #12 from a/b"), 12)
        self.assertEqual(history.pr_number("feat: thing (#478)"), 478)
        self.assertIsNone(history.pr_number("chore: no pr"))

    def test_rule_tags(self):
        text = "[CA-01][blocker] x SHA-256 [Q-01] [NEW] CA-02 [R-123]"
        self.assertEqual(history.RULE_ID.findall(text), ["CA-01", "Q-01", "NEW", "R-123"])


if __name__ == "__main__":
    unittest.main()
