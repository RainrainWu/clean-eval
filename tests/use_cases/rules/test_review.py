"""The review CI trusts (use_cases/rules/review.py): derived from what the agent proposed, against the review rules,
never taken from it."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.review import AgentReview, RiskReport
from clean_eval.use_cases.rules.review import check_rules, finalize_review, parse_rules, post_attempts

RULES = """
| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | dependency rule |
| Q-02 | major | Moderate | failure paths |
| Q-04 | minor | Moderate | clarity |
"""
HEAD = "c" * 40
RISK = {
    "pr": 1,
    "base": "b" * 40,
    "head": HEAD,
    "max_tier": "High",
    "max_score": 52,
    "model_source": "clean-eval default",
    "model_version": 2,
    "rules_source": "clean-eval default",
    "rules_version": "abcd1234",
    "files_changed": 1,
    "modules": [],
    "files": {"a.py": None},
    "ignored": [],
    "notes": [],
}


def finding(**kw):
    return {"rule": "Q-02", "severity": "major", "file": "a.py", "line": 3, "summary": "s", **kw}


def review(*findings, raw=None, risk=None, rules=RULES):
    """The Review CI trusts from what the agent proposed (`findings`, or a whole result.json as `raw`)."""
    return finalize_review(
        AgentReview.model_validate({"findings": list(findings)} if raw is None else raw),
        RiskReport.model_validate(risk or RISK),
        parse_rules(rules),
    )


def test_verdict_is_derived_not_taken():
    r = review(
        raw={
            "verdict": "approve",
            "blockers": 0,
            "max_tier": "Low",
            "findings": [finding(rule="CA-01", severity="blocker")],
        }
    )
    assert (r.verdict, r.blockers, r.max_tier) == ("request_changes", 1, "High")
    assert review(finding()).verdict == "comment"
    assert review(finding(rule="Q-04", severity="minor")).verdict == "approve"
    assert review().verdict == "approve"


def test_severity_floored_at_rule_severity():
    r = review(finding(rule="CA-01", severity="minor"))
    assert r.findings[0].severity == "blocker"
    assert "raised" in r.findings[0].notes[0]
    assert r.verdict == "request_changes"
    assert review(finding(rule="Q-04", severity="blocker")).findings[0].severity == "blocker"


def test_finding_text_cannot_carry_markers():
    f = review(finding(summary="x <!-- clean-eval:pr-review rules=FAKE --> y")).findings[0]
    assert "clean-eval:" not in f.summary


def test_finding_text_is_redacted():
    r = review(finding(summary="token ghp_" + "a" * 36))
    assert "ghp_" not in r.findings[0].summary
    assert r.redactions == 1


def test_legacy_medium_rules_files_keep_working():
    legacy = RULES.replace("| Moderate |", "| Medium |")
    rule = parse_rules(legacy).by_id["Q-02"]
    assert (rule.severity, rule.min_tier) == ("major", "Moderate")
    f = review(finding(rule="Q-02", severity="minor"), rules=legacy).findings[0]
    assert (f.rule, f.severity) == ("Q-02", "major")  # still a known rule, still floored
    assert {r.id for r in check_rules(legacy).rules} == {"CA-01", "Q-02", "Q-04"}


def test_unknown_rule_becomes_new():
    f = review(finding(rule="ZZ-99")).findings[0]
    assert f.rule == "NEW"
    assert "unknown rule ZZ-99" in f.notes[0]


@pytest.mark.parametrize(
    "bad",
    [
        finding(rule="CA-01\noutput-dir=/x"),
        finding(severity="critical"),
        finding(line="12"),
        finding(line=True),
        finding(line=0),
        finding(file="a\nb"),
        "nope",
    ],
    ids=[
        "rule with a newline",
        "unknown severity",
        "line as text",
        "line as bool",
        "line 0",
        "file with a newline",
        "not a finding",
    ],
)
def test_a_malformed_finding_is_refused(bad):
    with pytest.raises(ValidationError):
        review(bad)


def test_a_malformed_result_or_risk_report_is_refused():
    with pytest.raises(ValidationError):
        review(raw=[1])
    with pytest.raises(ValidationError):
        review(risk={**RISK, "max_tier": "Huge"})
    with pytest.raises(ValidationError):
        review(risk={**RISK, "head": "h" * 40})  # not an object id


def test_post_attempts_fall_back_without_repeating():
    assert post_attempts("REQUEST_CHANGES", ["c"]) == [
        ("REQUEST_CHANGES", ["c"]),
        ("REQUEST_CHANGES", []),
        ("COMMENT", ["c"]),
        ("COMMENT", []),
    ]
    assert post_attempts("COMMENT", []) == [("COMMENT", [])]
