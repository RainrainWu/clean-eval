"""The CI models (domain/ci.py): step outputs that carry only enums and numbers, and the tool calls the guard decides."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.ci import StepOutputs, ToolCall


def test_step_outputs_are_names_and_values():
    assert StepOutputs({"max-tier": "High", "auc": ""}).root == {"max-tier": "High", "auc": ""}


@pytest.mark.parametrize("bad", [{"verdict": "approve\nresult=/etc/passwd"}, {"Bad Name": "x"}, {"auc": 0.5}], ids=repr)
def test_step_outputs_are_single_lines(bad):
    with pytest.raises(ValidationError):
        StepOutputs(bad)


def test_tool_calls():
    call = ToolCall.model_validate_json('{"tool_name": "Read", "tool_input": null, "session_id": "s"}')
    assert (call.tool_name, call.tool_input, call.cwd) == ("Read", {}, None)


@pytest.mark.parametrize("bad", ['{"tool_name": ["Read"]}', '{"tool_input": "x"}', "[1, 2]", "not json"])
def test_malformed_tool_calls_are_refused(bad):
    with pytest.raises(ValidationError):
        ToolCall.model_validate_json(bad)
