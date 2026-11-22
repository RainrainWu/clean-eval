"""Calibration (use_cases/rules/calibration.py): how well the risk scores ranked the PRs that escaped defects."""

from clean_eval.domain.change import ModuleRecord
from clean_eval.domain.history import MergedPR
from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules import calibration


def test_auc():
    assert calibration.auc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert calibration.auc([0.5], [0.5]) == 0.5
    assert calibration.auc([], [0.1]) is None


def test_rescore_unrated_modules_take_the_policy_band():
    unit = MergedPR(
        sha="a" * 40,
        base="b" * 40,
        author="a",
        ts=1,
        subject="s",
        modules=[ModuleRecord(module="infra", band="Moderate", rated=False)],
    )
    prs = calibration.rescore([unit], Policy.from_overrides({"unrated_band": "High"}))
    assert (prs[0].max_tier, prs[0].max_score) == ("High", 50)
