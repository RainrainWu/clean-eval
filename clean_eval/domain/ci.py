"""What crosses into and out of GitHub Actions around the CI agent: the tool calls it asks to make, and step outputs."""
from typing import Annotated, Any, Optional

from pydantic import BaseModel, Field, RootModel, field_validator

OutputName = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]*$")]
OutputValue = Annotated[str, Field(pattern=r"^[^\r\n]*$")]  # one `name=value` line: a line break would start another


class StepOutputs(RootModel[dict[OutputName, OutputValue]]):
    """Step outputs: enums and numbers clean-eval built, never agent text."""


class ToolCall(BaseModel):
    """One tool call the CI agent wants to make, as Claude Code hands it to the PreToolUse hook."""
    tool_name: str = ""
    tool_input: dict[str, Any] = {}
    cwd: Optional[str] = None

    @field_validator("tool_name", mode="before")
    @classmethod
    def _no_name(cls, name):
        return "" if name is None else name

    @field_validator("tool_input", mode="before")
    @classmethod
    def _no_input(cls, tool_input):
        return {} if tool_input is None else tool_input
