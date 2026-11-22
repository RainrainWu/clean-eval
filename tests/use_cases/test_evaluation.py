"""The eval's use cases (use_cases/evaluation.py), with their dependencies mocked: collecting the merged-PR history of a
period, with every PR rated at its base and its escaped defects attributed, and each PR's review signal from the code
host."""

from unittest.mock import call

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.domain.errors import (
    HeatmapError,
    RemoteError,
    RemoteFailure,
    RunFailed,
)
from clean_eval.domain.evaluation import (
    FetchFailure,
    ReviewSignal,
)
from clean_eval.domain.policy import Band
from clean_eval.use_cases.evaluation import (
    collect_history,
    review_of,
    run_eval_action,
)
from clean_eval.use_cases.rules import evaluation


class TestCollectHistory:
    """The history of a period, as eval-history.json holds it (`collected`, over `fix_and_revert_history`: a period
    with a fix, a revert and a docs-only PR): every merged PR rated at its base, with the later fixes and reverts that
    touched its files attributed to it."""

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
        assert billing_pr["max_tier"] in list(Band)
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
        [
            (
                lf("two_prs_history"),
                {
                    "review_threads.side_effect": RemoteError(
                        "HTTP 403: rate limit exceeded",
                    )
                },
            )
        ],
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

    @pytest.mark.parametrize(
        "host",
        [
            {
                "require_auth.side_effect": HeatmapError(
                    "not logged in",
                )
            }
        ],
        indirect=True,
    )
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

    @pytest.mark.parametrize("host", [{"review_threads.return_value": lf("review_threads")}], indirect=True)
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

    @pytest.mark.parametrize("host", [{"review_threads.return_value": lf("review_threads")}], indirect=True)
    def test_the_trusted_reviewers_can_be_named(
        self,
        host,
    ):
        as_human = review_of(host, 7, ["human"])
        assert isinstance(as_human, ReviewSignal)
        assert as_human.clean_eval_marker == {"tier": "Low"}

    @pytest.mark.parametrize(
        "host",
        [
            {
                "review_threads.side_effect": RemoteError(
                    "no PR 8",
                    RemoteFailure.NOT_FOUND,
                )
            }
        ],
        indirect=True,
    )
    def test_a_failed_fetch_is_recorded_with_its_kind(
        self,
        host,
    ):
        assert review_of(host, 8) == FetchFailure(
            error="not_found",
            message="no PR 8",
        )
        host.review_threads.assert_called_once_with(8)


class TestRunEvalAction:
    """The eval's CI run refuses what it can't run on before it asks the code host or runs the agent. (The settings
    refuse a date that isn't one: tests/test_settings.py.)"""

    @pytest.mark.parametrize("environ", [{"CE_SINCE": "2026-02-01", "CE_UNTIL": "2026-01-01"}], indirect=True)
    def test_a_period_that_ends_before_it_starts_is_refused(
        self,
        git,
        fs,
        clock,
        env,
        host,
        agent,
        run_log,
        settings,
        run_folders,
    ):
        with pytest.raises(RunFailed, match=r"the eval's period: since \(2026-02-01\) is after until"):
            run_eval_action(git, fs, clock, env, host, agent, run_log, run_folders, settings, "")
        host.require_auth.assert_not_called()

    @pytest.mark.parametrize("environ", [{"CE_APPLY": "true"}], indirect=True)
    @pytest.mark.parametrize("links", [{"/repo/.clean-eval": "/elsewhere"}], indirect=True)
    def test_apply_refuses_a_symlinked_clean_eval(
        self,
        git,
        fs,
        clock,
        env,
        host,
        agent,
        run_log,
        settings,
        run_folders,
    ):
        with pytest.raises(RunFailed, match="symlink"):
            run_eval_action(git, fs, clock, env, host, agent, run_log, run_folders, settings, "")
        agent.run.assert_not_called()
