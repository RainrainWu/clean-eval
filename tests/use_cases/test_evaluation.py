"""The eval's use cases (use_cases/evaluation.py), with their dependencies mocked: collecting the merged-PR history of a
period, with every PR rated at its base and its escaped defects attributed, and each PR's review signal from the code
host."""

from unittest.mock import call

import pytest

from clean_eval.domain.errors import HeatmapError, RemoteError
from clean_eval.domain.evaluation import FetchFailure, ReviewSignal
from clean_eval.domain.policy import BAND_NAMES
from clean_eval.use_cases.evaluation import collect_history, review_of
from clean_eval.use_cases.rules import evaluation

# A period with a fix, a revert and a docs-only PR: #3 fixes the billing code #1 changed, and #5 reverts #4.
MERGED = [
    {
        "subject": "init",
        "files": {"src/billing/a.py": "a\n" * 20, "src/users/u.py": "u\n" * 20},
        "when": "2026-01-01T12:00",
    },
    {"subject": "feat: billing discounts (#1)", "files": {"src/billing/a.py": "b\n" * 25}, "when": "2026-02-01T12:00"},
    {"subject": "docs: notes (#2)", "files": {"docs/n.md": "n\n"}, "when": "2026-02-02T12:00"},
    {"subject": "feat: users export (#4)", "files": {"src/users/u.py": "v\n" * 22}, "when": "2026-02-03T12:00"},
    {"subject": "fix: billing rounding (#3)", "files": {"src/billing/a.py": "c\n" * 25}, "when": "2026-02-05T12:00"},
    {
        "subject": 'Revert "feat: users export (#4)" (#5)',
        "files": {"src/users/u.py": "u\n" * 20},
        "when": "2026-02-06T12:00",
    },
]
# Two PRs that change one file.
TWO_PRS = [
    {"subject": "init", "files": {"src/a/x.py": "1\n"}, "when": "2026-01-01T12:00"},
    {"subject": "feat: a (#1)", "files": {"src/a/x.py": "2\n"}, "when": "2026-02-01T12:00"},
    {"subject": "feat: b (#2)", "files": {"src/a/x.py": "3\n"}, "when": "2026-02-02T12:00"},
]
# A PR's review threads: clean-eval's review and a human's, who cites a rule and forges the marker, and inline comments.
VIEW = {
    "url": "u",
    "labels": [],
    "additions": 1,
    "deletions": 1,
    "comments": [],
    "reviews": [
        {
            "author": {"login": "clean-eval[bot]"},
            "body": "[CA-01][blocker] x\n<!-- clean-eval:pr-review tier=High -->",
        },
        {"author": {"login": "human"}, "body": "also [Q-02] maybe <!-- clean-eval:pr-review tier=Low -->"},
    ],
}
INLINE = [
    {"user": {"login": "clean-eval[bot]"}, "body": "[NEW][major] y"},
    {"user": {"login": "clean-eval[bot]"}, "body": "[R-03][blocker] z"},
]
THREADS = {"review_threads.return_value": (VIEW, INLINE)}


@pytest.mark.parametrize("collected", [{"history": MERGED}], indirect=True, scope="class")
class TestCollectHistory:
    """The history of a period, as eval-history.json holds it: every merged PR rated at its base, with the later fixes
    and reverts that touched its files attributed to it."""

    def test_units_and_pr_numbers(
        self,
        collected_json,
        collected_prs,
    ):
        assert sorted(collected_prs) == [1, 2, 3, 4, 5]
        assert collected_json["period"]["branch"] == "main"

    def test_fix_attributed_heuristically(
        self,
        collected_prs,
    ):
        defects = collected_prs[1]["defects"]
        assert len(defects) == 1
        assert (defects[0]["fix_pr"], defects[0]["kind"], defects[0]["confidence"]) == (3, "fix", "heuristic")
        assert defects[0]["overlap_files"] == ["src/billing/a.py"]

    def test_revert_attributed_with_high_confidence(
        self,
        collected_prs,
    ):
        defects = collected_prs[4]["defects"]
        assert (defects[0]["fix_pr"], defects[0]["kind"], defects[0]["confidence"]) == (5, "revert", "high")

    def test_docs_pr_has_no_modules_or_defects(
        self,
        collected_prs,
    ):
        assert collected_prs[2]["modules"] == []
        assert collected_prs[2]["defects"] == []

    def test_totals(
        self,
        collected_json,
    ):
        assert collected_json["totals"] == {"prs": 5, "defective_prs": 2, "escape_rate": 0.4}

    def test_prs_carry_the_base_heat_map(
        self,
        collected_prs,
    ):
        billing_pr = collected_prs[1]
        assert billing_pr["max_tier"] in BAND_NAMES
        billing = [module for module in billing_pr["modules"] if module["module"] == "src"][0]
        assert billing["rated"]
        assert set(billing["scores"]) == {"biz", "effects", "entropy", "unverified", "overall"}
        assert billing["score"] == billing["scores"]["overall"]
        assert billing["facts"]["name"] == "src"

    def test_a_replay_of_the_same_policy_reproduces_the_calibration(
        self,
        collected,
        policy,
    ):
        same = evaluation.replay(collected, policy)
        assert set(same.model_dump()) == {"auc_max_score", "by_band", "band_mix"}
        assert same.by_band == collected.calibration.by_band  # facts are stored exactly

    def test_sensitivity_varies_the_policy_one_knob_at_a_time(
        self,
        collected,
        policy,
    ):
        rows = evaluation.sensitivity(collected, policy)
        assert rows[0].change == "baseline"
        changes = [row.change for row in rows]
        assert len(changes) == len(set(changes))
        assert any(change.startswith("business_weights.dependents") for change in changes)
        assert any(change.startswith("overall_peak_share") for change in changes)
        assert any(change.startswith("unverified_mix.links") for change in changes)


class TestCollectHistoryWithACodeHost:
    """With a CodeHost, collecting needs its auth first, and then fetches each PR's reviews."""

    @pytest.mark.parametrize(
        ("history", "host"),
        [(TWO_PRS, {"review_threads.side_effect": RemoteError("HTTP 403: rate limit exceeded")})],
        indirect=True,
    )
    def test_failed_fetches_flag_the_rule_metrics_unreliable(
        self,
        git,
        clock,
        policy,
        period,
        host,
    ):
        rules = collect_history(git, clock, policy, period, host).rules
        assert rules is not None
        assert sorted(rules.prs_fetch_failed) == [1, 2]
        assert not rules.reliable
        assert rules.prs_reviewed == 0
        assert evaluation.rules_warning(rules) == "gh review fetch failed for 2/2 PRs (100%) - rule metrics unreliable"
        assert host.review_threads.call_args_list == [call(1), call(2)]

    @pytest.mark.parametrize("host", [{"require_auth.side_effect": HeatmapError("not logged in")}], indirect=True)
    def test_collecting_needs_the_code_hosts_auth(
        self,
        git,
        clock,
        policy,
        period,
        host,
    ):
        with pytest.raises(HeatmapError, match="not logged in"):
            collect_history(git, clock, policy, period, host)
        git.first_parent_log.assert_not_called()  # nothing is read before the auth is checked


class TestReviewOf:
    """One PR's review signal, from its review threads on the code host; a failed fetch is recorded instead."""

    @pytest.mark.parametrize("host", [THREADS], indirect=True)
    def test_rules_are_counted_from_the_clean_eval_reviewer_only(
        self,
        host,
    ):
        signal = review_of(host, 7)
        assert isinstance(signal, ReviewSignal)
        assert signal.rules_cited == ["CA-01", "R-03"]  # the human's [Q-02] is ignored
        assert signal.new_findings == 1
        assert signal.clean_eval_marker == {"tier": "High"}  # the human's forged marker doesn't count
        assert signal.untrusted_markers_ignored == 1
        host.review_threads.assert_called_once_with(7)

    @pytest.mark.parametrize("host", [THREADS], indirect=True)
    def test_the_trusted_reviewers_can_be_named(
        self,
        host,
    ):
        as_human = review_of(host, 7, ["human"])
        assert isinstance(as_human, ReviewSignal)
        assert as_human.clean_eval_marker == {"tier": "Low"}

    @pytest.mark.parametrize(
        "host", [{"review_threads.side_effect": RemoteError("no PR 8", "not_found")}], indirect=True
    )
    def test_a_failed_fetch_is_recorded_with_its_kind(
        self,
        host,
    ):
        assert review_of(host, 8) == FetchFailure(error="not_found", message="no PR 8")
        host.review_threads.assert_called_once_with(8)
