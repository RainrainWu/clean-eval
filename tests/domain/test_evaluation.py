"""The eval's models (domain/evaluation.py): periods, merged PRs and their review signal, and the tuning edits and
their outcome."""

import datetime as dt

import pytest
from pydantic import ValidationError

from clean_eval.domain.evaluation import (
    AgentEvalResult,
    EvalResult,
    FetchFailure,
    MergedPR,
    ModelChange,
    Period,
    PolicyEdit,
    Rejection,
    ReviewSignal,
    RulesChange,
)

# The merged-PR history models (domain/evaluation.py): periods, merged PRs and their review signal.

SHA = "a" * 40


def test_period():
    p = Period(since="2026-01-01", until="2026-01-31", window_days=10)
    assert p.lookahead_until == dt.date(2026, 2, 10)
    assert p.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp()


@pytest.mark.parametrize(
    "bad",
    [
        {"since": "2026-02-01", "until": "2026-01-01"},
        {"since": "last week", "until": "2026-01-01"},
        {"since": "2026-01-01", "until": "2026-02-30"},
    ],
    ids=["ends before it starts", "not a date", "no such day"],
)
def test_an_invalid_period_is_refused(bad):
    with pytest.raises(ValidationError):
        Period(**bad)


def test_merged_prs_keep_a_file_count_not_the_files():
    u = MergedPR(sha=SHA, base="b" * 40, author="a", ts=1, subject="s", files=["x.py", "y.py"])
    dumped = u.model_dump()
    assert "files" not in dumped
    assert MergedPR.model_validate(dumped).file_count == 2


def test_a_fetch_failure_never_passes_for_a_review():
    failed = MergedPR.model_validate(
        {
            "sha": SHA,
            "base": SHA,
            "author": "a",
            "ts": 1,
            "subject": "s",
            "review": {"error": "not_found", "message": "gone"},
        }
    )
    assert isinstance(failed.review, FetchFailure)
    reviewed = MergedPR.model_validate(
        {"sha": SHA, "base": SHA, "author": "a", "ts": 1, "subject": "s", "review": {"rules_cited": ["CA-01"]}}
    )
    assert isinstance(reviewed.review, ReviewSignal)


# The eval's tuning models (domain/evaluation.py): proposed policy and rules edits, and their outcome.


class TestTuningModels:
    def test_policy_edits(self):
        edit = PolicyEdit(new={"business_weights": {"dependents": 0.45}, "_why": "x"}, orig={"_why": "y"})
        assert edit.changed_keys == {"business_weights"}

    def test_recommendations_are_text(self):
        r = AgentEvalResult.model_validate(
            {
                "recommendations": [{"target": 1, "change": None, "evidence": "e" * 3000}, "junk"]
                + [{"target": "t"}] * 40,
                "auc": 0.99,
            }
        )
        assert len(r.recommendations) == 30
        first = r.recommendations[0]
        assert (first.target, first.change, len(first.evidence)) == ("1", "None", 2000)

    def test_applied_is_derived(self):
        def result(**change):
            return EvalResult(since="2026-01-01", until="2026-02-01", prs=30, defective_prs=6, auc=0.7, **change)

        assert not result(model_change=Rejection(reason="no")).applied
        assert result(rules_change=RulesChange()).applied
        accepted = result(model_change=ModelChange(changed_keys=["x"], auc_before=0.6, auc_after=0.7))
        assert EvalResult.model_validate(accepted.model_dump()).model_change == accepted.model_change


class TestModelChange:
    def test_tunable_change_ok(self):
        new = {"business_weights": {"dependents": 0.45}, "unrated_band": "High", "_why": "a comment"}
        assert PolicyEdit(new=new).changed_keys == {"business_weights", "unrated_band"}
        assert PolicyEdit(new={"version": 3}, orig={"version": 2}).changed_keys == {"version"}

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
    def test_structural_or_bad_changes_rejected(self, new):
        with pytest.raises(ValidationError):
            PolicyEdit(new=new)
