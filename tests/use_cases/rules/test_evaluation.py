"""The eval's rules (use_cases/rules/evaluation.py): reading the merged-PR history, and calibration."""

import pytest

from clean_eval.use_cases.rules.evaluation import RULE_ID, auc, pr_number, rescore

# Review text with rule tags among other bracketed words and IDs, and the tags pr-review wrote.
TAGGED = "[CA-01][blocker] x SHA-256 [Q-01] [NEW] CA-02 [R-123]"
TAGS = ["CA-01", "Q-01", "NEW", "R-123"]
# A PR that touched only a module no rated module covers, in the band unrated modules had then.
UNRATED_ONLY = [{"modules": [{"module": "infra", "band": "Moderate", "rated": False}]}]


class TestPrNumber:
    """The PR a first-parent commit merged, from its subject: a merge commit's number, or the last `(#n)` of a squash
    commit; none for a direct push."""

    @pytest.mark.parametrize(
        ("subject", "number"),
        [
            ("Fix #45 (#67)", 67),
            ("Merge pull request #12 from a/b", 12),
            ("feat: thing (#478)", 478),
            ("chore: no pr", None),
        ],
        ids=["squash that names an issue", "merge commit", "squash", "direct push"],
    )
    def test_the_pr_comes_from_the_subject(
        self,
        subject,
        number,
    ):
        assert pr_number(subject) == number


class TestRuleId:
    """The rule tags pr-review writes in its reviews ([CA-01], [NEW])."""

    def test_only_bracketed_rule_ids_and_new_are_tags(
        self,
    ):
        assert RULE_ID.findall(TAGGED) == TAGS


class TestAuc:
    """How well scores rank the defective PRs above the clean ones: ties count half, and without PRs on one side it is
    undefined."""

    @pytest.mark.parametrize(
        ("defective", "clean", "expected"),
        [([0.9, 0.8], [0.1, 0.2], 1.0), ([0.5], [0.5], 0.5), ([], [0.1], None)],
        ids=["every defective PR scores higher", "a tie", "no defective PR"],
    )
    def test_auc(
        self,
        defective,
        clean,
        expected,
    ):
        assert auc(defective, clean) == expected


class TestRescore:
    """Replaying the history under another policy: every module the PRs touched is re-scored."""

    @pytest.mark.parametrize("merged_prs", [UNRATED_ONLY], indirect=True)
    @pytest.mark.parametrize("policy", [{"unrated_band": "High"}], indirect=True)
    def test_unrated_modules_take_the_policys_band(
        self,
        merged_prs,
        policy,
    ):
        (replayed,) = rescore(merged_prs, policy)
        assert (replayed.max_tier, replayed.max_score) == ("High", 50)
