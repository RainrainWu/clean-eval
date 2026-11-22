"""The CI models (domain/ci.py): step outputs that carry only enums and numbers, and the tool calls the guard decides."""
import unittest

from pydantic import ValidationError

from clean_eval.domain.ci import StepOutputs, ToolCall


class CiModels(unittest.TestCase):
    def test_step_outputs_are_single_lines(self):
        self.assertEqual(StepOutputs({"max-tier": "High", "auc": ""}).root, {"max-tier": "High", "auc": ""})
        for bad in ({"verdict": "approve\nresult=/etc/passwd"}, {"Bad Name": "x"}, {"auc": 0.5}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                StepOutputs(bad)

    def test_tool_calls(self):
        call = ToolCall.model_validate_json('{"tool_name": "Read", "tool_input": null, "session_id": "s"}')
        self.assertEqual((call.tool_name, call.tool_input, call.cwd), ("Read", {}, None))
        for bad in ('{"tool_name": ["Read"]}', '{"tool_input": "x"}', '[1, 2]', 'not json'):
            with self.assertRaises(ValidationError, msg=bad):
                ToolCall.model_validate_json(bad)


if __name__ == "__main__":
    unittest.main()
