"""The review models (domain/review.py): what an agent's proposed review may hold, and the review CI derives."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.review import AgentReview, CheckedRules, Finding, PullRequest, Review, ReviewRule

SHA = "a" * 40


def finding(**kw):
    return {"rule": "Q-02", "severity": "major", "file": "a.py", "line": 3, "summary": "s", **kw}


@pytest.mark.parametrize(
    "bad",
    [
        {"rule": "CA-01\noutput-dir=/x"},
        {"rule": "lowercase-1"},
        {"severity": "critical"},
        {"line": "12"},
        {"line": True},
        {"line": 0},
        {"line": 1.0},
        {"file": "a\nb"},
        {"file": 5},
        {"summary": None},
    ],
    ids=repr,
)
def test_agent_findings_are_checked_field_by_field(bad):
    with pytest.raises(ValidationError):
        AgentReview.model_validate({"findings": [finding(**bad)]})


def test_agent_text_is_cut_and_extras_are_ignored():
    r = AgentReview.model_validate(
        {
            "verdict": "approve",
            "blockers": 0,
            "findings": [finding(file="", fix=None, summary="x" * 2000)] * 60,
        }
    )
    assert len(r.findings) == 50
    f = r.findings[0]
    assert (f.file, f.fix, len(f.summary)) == (None, "", 1500)
    assert AgentReview.model_validate({"findings": None}).findings == []


def test_the_verdict_and_counts_are_derived():
    review = Review(
        max_tier="High",
        max_score=52,
        findings=[Finding(rule="CA-01", severity="blocker"), Finding(rule="Q-02", severity="major")],
    )
    assert (review.verdict, review.blockers, review.majors) == ("request_changes", 1, 1)
    stored = {**review.model_dump(), "verdict": "approve", "blockers": 0}  # a tampered result.json
    assert Review.model_validate(stored).verdict == "request_changes"
    assert Review(max_tier="Low", max_score=0).verdict == "approve"
    with pytest.raises(ValidationError):
        Review(max_tier="Huge", max_score=10)


def test_rules_tables():
    assert ReviewRule(id="Q-02", severity="major", min_tier="Medium").min_tier == "Moderate"
    rule = {"id": "CA-01", "severity": "blocker", "min_tier": "Low"}
    CheckedRules(rules=[rule])
    for bad in ([], [rule, rule]):
        with pytest.raises(ValidationError):
            CheckedRules(rules=bad)


def test_pull_request_commits_are_object_ids():
    pr = {
        "number": 7,
        "baseRefName": "main",
        "headRefName": "f",
        "baseRefOid": SHA,
        "headRefOid": "b" * 40,
        "author": {"login": "dev"},
    }
    assert PullRequest.model_validate(pr).author == "dev"


@pytest.mark.parametrize("bad", ["HEAD", "a" * 39, "--upload-pack=x", "g" * 40])
def test_a_pull_requests_head_must_be_an_object_id(bad):
    pr = {"number": 7, "baseRefName": "main", "headRefName": "f", "baseRefOid": SHA, "author": {"login": "dev"}}
    with pytest.raises(ValidationError):
        PullRequest.model_validate({**pr, "headRefOid": bad})
