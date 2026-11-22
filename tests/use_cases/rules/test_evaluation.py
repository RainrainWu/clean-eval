"""The eval's rules (use_cases/rules/evaluation.py): reading the merged-PR history, and calibration."""

from clean_eval.domain.evaluation import MergedPR
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import ModuleRecord
from clean_eval.use_cases.rules import evaluation

# Reading the merged-PR history (use_cases/rules/evaluation.py): PR numbers and rule tags in commit and review text.


def test_pr_number():
    assert evaluation.pr_number("Fix #45 (#67)") == 67
    assert evaluation.pr_number("Merge pull request #12 from a/b") == 12
    assert evaluation.pr_number("feat: thing (#478)") == 478
    assert evaluation.pr_number("chore: no pr") is None


def test_rule_tags():
    text = "[CA-01][blocker] x SHA-256 [Q-01] [NEW] CA-02 [R-123]"
    assert evaluation.RULE_ID.findall(text) == ["CA-01", "Q-01", "NEW", "R-123"]


# Calibration (use_cases/rules/evaluation.py): how well the risk scores ranked the PRs that escaped defects.


def test_auc():
    assert evaluation.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert evaluation.auc([0.5], [0.5]) == 0.5
    assert evaluation.auc([], [0.1]) is None


def test_rescore_unrated_modules_take_the_policy_band():
    unit = MergedPR(
        sha="a" * 40,
        base="b" * 40,
        author="a",
        ts=1,
        subject="s",
        modules=[ModuleRecord(module="infra", band="Moderate", rated=False)],
    )
    prs = evaluation.rescore([unit], Policy.from_overrides({"unrated_band": "High"}))
    assert (prs[0].max_tier, prs[0].max_score) == ("High", 50)
