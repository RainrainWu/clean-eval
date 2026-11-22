"""The eval's models (domain/evaluation.py): periods, merged PRs and their review signal, and the tuning edits and
their outcome."""

import datetime as dt

import pytest
from pydantic import ValidationError
from pytest_lazy_fixtures import lf

from clean_eval.domain.evaluation import (
    AgentEvalResult,
    EvalResult,
    FetchFailure,
    MergedPR,
    Period,
    PolicyEdit,
    Rejection,
    ReviewSignal,
    RulesChange,
)


class TestPeriod:
    """The PRs merged from `since` to `until` (inclusive days), and how long after each one a fix still counts."""

    @pytest.mark.parametrize("period", [{"window_days": 10}], indirect=True, ids=["a 10-day window"])
    def test_a_period_ends_with_its_last_day_and_looks_ahead_its_window(
        self,
        period,
    ):
        read = Period.model_validate(period)
        assert read.lookahead_until == dt.date(2026, 2, 10)
        assert read.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp()

    @pytest.mark.parametrize(
        "period",
        [
            {"since": "2026-02-01", "until": "2026-01-01"},
            {"since": "last week", "until": "2026-01-01"},
            {"since": "2026-01-01", "until": "2026-02-30"},
        ],
        indirect=True,
        ids=["ends before it starts", "not a date", "no such day"],
    )
    def test_an_invalid_period_is_refused(
        self,
        period,
    ):
        with pytest.raises(ValidationError):
            Period.model_validate(period)


class TestMergedPR:
    """One merged PR: it stores how many files it changed, not which, and a review that couldn't be fetched never
    passes for a review."""

    @pytest.mark.parametrize("merged_pr", [{"files": ["x.py", "y.py"]}], indirect=True, ids=["two files"])
    def test_merged_prs_keep_a_file_count_not_the_files(
        self,
        merged_pr,
    ):
        dumped = MergedPR.model_validate(merged_pr).model_dump()
        assert "files" not in dumped
        assert MergedPR.model_validate(dumped).file_count == 2

    @pytest.mark.parametrize(
        ("merged_pr", "kind"),
        [
            ({"review": {"error": "not_found", "message": "gone"}}, FetchFailure),
            ({"review": {"rules_cited": ["CA-01"]}}, ReviewSignal),
        ],
        indirect=["merged_pr"],
        ids=["a fetch failure", "a review"],
    )
    def test_a_fetch_failure_never_passes_for_a_review(
        self,
        merged_pr,
        kind,
    ):
        assert isinstance(MergedPR.model_validate(merged_pr).review, kind)


class TestPolicyEdit:
    """An edit of the risk-model overrides: comments are no change, only tunable knobs (and the version) may change,
    and the edit must still be a valid policy."""

    @pytest.mark.parametrize(
        ("new", "orig", "changed"),
        [
            ({"business_weights": {"dependents": 0.45}, "_why": "x"}, {"_why": "y"}, {"business_weights"}),
            (
                {"business_weights": {"dependents": 0.45}, "unrated_band": "High", "_why": "a comment"},
                {},
                {"business_weights", "unrated_band"},
            ),
            ({"version": 3}, {"version": 2}, {"version"}),
        ],
        ids=["a comment is no change", "tunable knobs", "the version"],
    )
    def test_the_changed_keys_are_the_knobs_that_differ(
        self,
        new,
        orig,
        changed,
    ):
        assert (
            PolicyEdit(
                new=new,
                orig=orig,
            ).changed_keys
            == changed
        )

    @pytest.mark.parametrize(
        "new",
        [
            {"depth": 1},
            {"exclude": ["**"]},
            {"tooling": "x"},
            {"business_weights": {"dependents": -1}},
            {"business_weights": {"unknown": 1}},
            {"unrated_band": "Huge"},
            {"overall_peak_share": 2},
            {"window_days": 30},
            {"version": "2"},
            {"effects_scale": 0},
            {"business_saturation": {"size": 0}},
            [1],
        ],
        ids=repr,
    )
    def test_structural_or_bad_changes_are_refused(
        self,
        new,
    ):
        with pytest.raises(ValidationError):
            PolicyEdit(
                new=new,
            )


class TestAgentEvalResult:
    """The eval agent's result.json: its recommendations, as text cut to size, objects only and at most 30; whatever
    else it writes (counts, AUC) is ignored."""

    def test_recommendations_are_text(
        self,
    ):
        result = AgentEvalResult.model_validate(
            {
                "recommendations": [{"target": 1, "change": None, "evidence": "e" * 3000}, "junk"]
                + [{"target": "t"}] * 40,
                "auc": 0.99,
            }
        )
        assert len(result.recommendations) == 30
        first = result.recommendations[0]
        assert (first.target, first.change, len(first.evidence)) == ("1", "None", 2000)


class TestEvalResult:
    """The eval CI trusts: whether it applied an edit is derived from the changes it kept, and a kept change survives
    being stored."""

    @pytest.mark.parametrize(
        ("eval_result", "applied"),
        [
            (
                {
                    "model_change": Rejection(
                        reason="no",
                    )
                },
                False,
            ),
            ({"rules_change": RulesChange()}, True),
            ({"model_change": lf("accepted_model_change")}, True),
        ],
        indirect=["eval_result"],
        ids=["a rejected model change", "an accepted rules change", "an accepted model change"],
    )
    def test_applied_is_derived(
        self,
        eval_result,
        applied,
    ):
        assert EvalResult.model_validate(eval_result).applied is applied

    @pytest.mark.parametrize(
        "eval_result",
        [{"model_change": lf("accepted_model_change")}],
        indirect=True,
        ids=["an accepted model change"],
    )
    def test_an_accepted_model_change_survives_a_dump_and_a_load(
        self,
        eval_result,
    ):
        accepted = EvalResult.model_validate(eval_result)
        assert EvalResult.model_validate(accepted.model_dump()).model_change == accepted.model_change
