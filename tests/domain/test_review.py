"""The PR review models (domain/review.py): what an agent's proposed review may hold, the review CI derives, the rules
tables, the pull request, and the CI run's step outputs and tool calls."""

import pytest
from pydantic import ValidationError
from pytest_lazy_fixtures import lf

from clean_eval.domain.review import (
    AgentReview,
    CheckedRules,
    PullRequest,
    Review,
    ReviewRule,
    StepOutputs,
    ToolCall,
)


class TestAgentReview:
    """The review agent's result.json: each finding is checked field by field, its text is cut to size, at most 50
    findings are read, and whatever else the agent writes is ignored."""

    @pytest.mark.parametrize(
        "finding",
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
        indirect=True,
        ids=repr,
    )
    def test_agent_findings_are_checked_field_by_field(
        self,
        finding,
    ):
        with pytest.raises(ValidationError):
            AgentReview.model_validate({"findings": [finding]})

    @pytest.mark.parametrize(
        "finding",
        [{"file": "", "fix": None, "summary": "x" * 2000}],
        indirect=True,
        ids=["no file, no fix, a long summary"],
    )
    def test_agent_text_is_cut_and_extras_are_ignored(
        self,
        finding,
    ):
        review = AgentReview.model_validate({"verdict": "approve", "blockers": 0, "findings": [finding] * 60})
        assert len(review.findings) == 50
        first = review.findings[0]
        assert (first.file, first.fix, len(first.summary)) == (None, "", 1500)

    def test_null_findings_read_as_no_findings(
        self,
    ):
        assert AgentReview.model_validate({"findings": None}).findings == []


class TestReview:
    """The review CI trusts: its verdict and counts are derived from its findings, never read back from a stored review,
    and its band must be a band name."""

    @pytest.mark.parametrize(
        ("stored_review", "expected"),
        [
            ({"findings": [lf("trusted_blocker"), lf("trusted_major")]}, ("request_changes", 1, 1)),
            ({"findings": [lf("trusted_major")]}, ("comment", 0, 1)),
            ({"max_tier": "Low", "max_score": 0}, ("approve", 0, 0)),
        ],
        indirect=["stored_review"],
        ids=["a blocker", "a major", "no findings"],
    )
    def test_the_verdict_and_counts_are_derived(
        self,
        stored_review,
        expected,
    ):
        review = Review.model_validate(stored_review)
        assert (review.verdict, review.blockers, review.majors) == expected

    @pytest.mark.parametrize(
        "stored_review", [{"findings": [lf("trusted_blocker"), lf("trusted_major")]}], indirect=True, ids=["a blocker"]
    )
    def test_a_tampered_verdict_is_derived_again(
        self,
        stored_review,
    ):
        tampered = {**Review.model_validate(stored_review).model_dump(), "verdict": "approve", "blockers": 0}
        assert Review.model_validate(tampered).verdict == "request_changes"

    @pytest.mark.parametrize("stored_review", [{"max_tier": "Huge", "max_score": 10}], indirect=True, ids=["Huge"])
    def test_the_band_must_be_a_band_name(
        self,
        stored_review,
    ):
        with pytest.raises(ValidationError):
            Review.model_validate(stored_review)


class TestReviewRule:
    """One row of a rules table: the pre-v2 band name still reads, and the min tier must be a band name."""

    @pytest.mark.parametrize("review_rule", [{"min_tier": "Medium"}], indirect=True, ids=["Medium"])
    def test_the_legacy_band_name_reads_as_its_v2_name(
        self,
        review_rule,
    ):
        assert ReviewRule.model_validate(review_rule).min_tier == "Moderate"

    @pytest.mark.parametrize(
        "review_rule",
        [{"min_tier": []}, {"min_tier": {}}, {"min_tier": 3}, {"min_tier": None}],
        indirect=True,
        ids=["list", "object", "number", "null"],
    )
    def test_a_rules_row_needs_a_band_name(
        self,
        review_rule,
    ):
        with pytest.raises(ValidationError):  # a validation error, never a TypeError from the legacy-name lookup
            ReviewRule.model_validate(review_rule)


class TestCheckedRules:
    """A rules file an unattended eval may keep: at least one rule row, and no ID twice."""

    def test_a_rules_file_with_distinct_rows_is_kept(
        self,
        review_rule,
    ):
        assert [rule.id for rule in CheckedRules.model_validate({"rules": [review_rule]}).rules] == ["CA-01"]

    @pytest.mark.parametrize("copies", [0, 2], ids=["no rule rows", "an ID twice"])
    def test_a_rules_file_without_rows_or_with_an_id_twice_is_refused(
        self,
        review_rule,
        copies,
    ):
        with pytest.raises(ValidationError):
            CheckedRules.model_validate({"rules": [review_rule] * copies})


class TestPullRequest:
    """A pull request as the code host describes it: its author is a login, and its commits must be object ids."""

    def test_a_pull_request_whose_commits_are_object_ids_is_read(
        self,
        pull_request,
    ):
        read = PullRequest.model_validate(pull_request)
        assert (read.author, read.baseRefOid, read.headRefOid) == ("dev", "a" * 40, "b" * 40)

    @pytest.mark.parametrize(
        "pull_request",
        [
            {"headRefOid": "HEAD"},
            {"headRefOid": "a" * 39},
            {"headRefOid": "--upload-pack=x"},
            {"headRefOid": "g" * 40},
        ],
        indirect=True,
        ids=["a ref name", "too short", "an option", "not hexadecimal"],
    )
    def test_a_pull_requests_head_must_be_an_object_id(
        self,
        pull_request,
    ):
        with pytest.raises(ValidationError):
            PullRequest.model_validate(pull_request)


class TestStepOutputs:
    """Step outputs: names and one-line texts that clean-eval built (enums and numbers), never agent text."""

    def test_step_outputs_are_names_and_values(
        self,
    ):
        assert StepOutputs(
            {"max-tier": "High", "auc": ""},
        ).root == {"max-tier": "High", "auc": ""}

    @pytest.mark.parametrize(
        "outputs",
        [{"verdict": "approve\nresult=/etc/passwd"}, {"Bad Name": "x"}, {"auc": 0.5}],
        ids=repr,
    )
    def test_step_outputs_are_single_lines(
        self,
        outputs,
    ):
        with pytest.raises(ValidationError):
            StepOutputs(
                outputs,
            )


class TestToolCall:
    """A tool call the CI agent asks to make, as Claude Code hands it to the hook: what is missing reads as empty, and
    malformed JSON is refused."""

    def test_a_null_input_is_empty_and_unknown_keys_are_ignored(
        self,
    ):
        call = ToolCall.model_validate_json('{"tool_name": "Read", "tool_input": null, "session_id": "s"}')
        assert (call.tool_name, call.tool_input, call.cwd) == ("Read", {}, None)

    @pytest.mark.parametrize(
        "text",
        ['{"tool_name": ["Read"]}', '{"tool_input": "x"}', "[1, 2]", "not json"],
        ids=["a list for a name", "a string for an input", "not an object", "not JSON"],
    )
    def test_malformed_tool_calls_are_refused(
        self,
        text,
    ):
        with pytest.raises(ValidationError):
            ToolCall.model_validate_json(text)
