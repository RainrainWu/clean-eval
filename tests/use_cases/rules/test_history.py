"""Reading the merged-PR history (use_cases/rules/history.py): PR numbers and rule tags in commit and review text."""

from clean_eval.use_cases.rules import history


def test_pr_number():
    assert history.pr_number("Fix #45 (#67)") == 67
    assert history.pr_number("Merge pull request #12 from a/b") == 12
    assert history.pr_number("feat: thing (#478)") == 478
    assert history.pr_number("chore: no pr") is None


def test_rule_tags():
    text = "[CA-01][blocker] x SHA-256 [Q-01] [NEW] CA-02 [R-123]"
    assert history.RULE_ID.findall(text) == ["CA-01", "Q-01", "NEW", "R-123"]
