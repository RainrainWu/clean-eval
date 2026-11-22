"""The CI agent's PreToolUse guard (external/agent_guard.py): the tool call it reads, and an allowlist that resolves
real paths and fails closed. Paths resolve on the real file system, among the agent's folders of the `sandbox` fixture:
in a tool call, "<ws>" stands for the workspace (the read root holding the checkout), "<ctx>" for the context (the other
read root) and "<agent>" for the agent's output (the write root). The hook reads its roots from a mocked Environment
(`guard_env`), and its stdin and stderr are in memory."""

from pathlib import Path
from unittest.mock import call

import pytest
from pydantic import ValidationError

from clean_eval.external import agent_guard as guard


class TestReads:
    """Read, Grep, Glob and LS reach only real paths under a read root or the write root, and never inside .git."""

    @pytest.mark.parametrize(
        ("tool", "tool_input"),
        [
            pytest.param("Read", {"file_path": "<ws>/a.py"}, id="a workspace file"),
            pytest.param("Read", {"file_path": "<ctx>/diff.patch"}, id="a context file"),
            pytest.param("Read", {"file_path": "relative/b.py"}, id="relative to the cwd"),
            pytest.param("Grep", {"pattern": "TODO"}, id="grep in the cwd by default"),
            pytest.param("Glob", {"pattern": "**/*.py", "path": "<ws>"}, id="glob in the workspace"),
            pytest.param("Read", {"file_path": "<agent>/report.md"}, id="the agent's output"),
        ],
        indirect=["tool_input"],
    )
    def test_a_path_under_a_root_is_allowed(
        self,
        sandbox,
        tool,
        tool_input,
        fs,
    ):
        assert guard.decide(tool, tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs) is None

    @pytest.mark.parametrize(  # reads outside every root or into .git, and a word of the reason the guard gives
        ("tool", "tool_input", "expected"),
        [
            pytest.param("Read", {"file_path": "/etc/hosts"}, "outside", id="elsewhere"),
            pytest.param("Read", {"file_path": "<ws>/../secret.txt"}, "outside", id="up out of the workspace"),
            pytest.param("Grep", {"pattern": "KEY", "path": "/proc/self"}, "outside", id="grep elsewhere"),
            pytest.param("Read", {"file_path": "<ws>/link.txt"}, "outside", id="a symlink out of the workspace"),
            pytest.param("Read", {"file_path": "<ws>/.git/config"}, ".git", id="a file in .git"),
            pytest.param("Grep", {"pattern": "AUTHORIZATION", "path": "<ws>/.git"}, ".git", id="grep in .git"),
        ],
        indirect=["tool_input"],
    )
    def test_a_path_outside_the_roots_or_in_git_is_denied(
        self,
        sandbox,
        tool,
        tool_input,
        expected,
        fs,
    ):
        reason = guard.decide(tool, tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs)
        assert reason is not None
        assert expected in reason


class TestGlobs:
    """A glob resolves relative to its folder, so one that could escape it (absolute, going up, braces) or that targets
    .git is denied; a regex is no glob."""

    @pytest.mark.parametrize(
        ("tool", "tool_input", "expected"),
        [
            pytest.param("Glob", {"pattern": "/proc/*/environ"}, "relative", id="absolute"),
            pytest.param("Glob", {"pattern": "../../*"}, "relative", id="up"),
            pytest.param("Glob", {"pattern": "{..,a}/secret.txt"}, "relative", id="braces"),
            pytest.param("Glob", {"pattern": "a/..x/../../b"}, "relative", id="up behind a name"),
            pytest.param("Grep", {"pattern": "x", "glob": "/etc/*"}, "relative", id="grep with an absolute glob"),
            pytest.param(
                "Grep", {"pattern": "AUTHORIZATION", "glob": ".git/config"}, ".git", id="grep with a glob into .git"
            ),
            pytest.param("Glob", {"pattern": "**/.git/**"}, ".git", id="into any .git"),
        ],
        indirect=["tool_input"],
    )
    def test_an_escaping_glob_is_denied(
        self,
        sandbox,
        tool,
        tool_input,
        expected,
        fs,
    ):
        reason = guard.decide(tool, tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs)
        assert reason is not None
        assert expected in reason

    def test_a_regex_pattern_is_fine(
        self,
        sandbox,
        fs,
    ):
        tool_input = {"pattern": "(a|b)", "glob": "**/*.py"}
        assert guard.decide("Grep", tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs) is None


class TestRoots:
    """The roots the guard is given, as real paths; a symlinked root would make its target reachable, so it is dropped
    (fail closed)."""

    def test_a_symlinked_root_is_dropped(
        self,
        sandbox,
        fs,
    ):
        Path(
            sandbox.base,
            "agent-link",
        ).symlink_to(sandbox.base)
        assert guard.roots(
            [
                str(
                    Path(
                        sandbox.base,
                        "agent-link",
                    )
                ),
                sandbox.agent,
            ],
            fs,
        ) == [sandbox.agent]


class TestWrites:
    """Write, Edit and MultiEdit reach only real paths under a write root."""

    @pytest.mark.parametrize(
        ("tool", "tool_input"),
        [
            pytest.param("Write", {"file_path": "<agent>/result.json"}, id="write"),
            pytest.param("Edit", {"file_path": "<agent>/report.md"}, id="edit"),
        ],
        indirect=["tool_input"],
    )
    def test_a_path_under_the_write_root_is_allowed(
        self,
        sandbox,
        tool,
        tool_input,
        fs,
    ):
        assert guard.decide(tool, tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs) is None

    @pytest.mark.parametrize(
        ("tool", "tool_input", "expected"),
        [
            pytest.param(
                "Write", {"file_path": "<ws>/clean_eval/domain/review.py"}, "outside", id="into the workspace"
            ),
            pytest.param("Edit", {"file_path": "<ctx>/risk.json"}, "outside", id="into the context"),
            pytest.param("Write", {}, "missing", id="no path"),
        ],
        indirect=["tool_input"],
    )
    def test_any_other_path_is_denied(
        self,
        sandbox,
        tool,
        tool_input,
        expected,
        fs,
    ):
        reason = guard.decide(tool, tool_input, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs)
        assert reason is not None
        assert expected in reason


class TestOtherTools:
    """Every other tool is denied (it could run code, reach the network or load instructions); the tools without file
    access are allowed."""

    @pytest.mark.parametrize(
        "tool", ["Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit", "Skill", "mcp__github__create_issue"]
    )
    def test_every_other_tool_is_denied(
        self,
        sandbox,
        tool,
        fs,
    ):
        assert (
            guard.decide(tool, {"command": "echo hi"}, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs)
            is not None
        )

    @pytest.mark.parametrize("tool", ["TodoWrite", "ToolSearch"])
    def test_a_tool_without_file_access_is_allowed(
        self,
        sandbox,
        tool,
        fs,
    ):
        assert guard.decide(tool, {}, sandbox.ws, sandbox.read_roots, sandbox.write_roots, fs) is None


class TestHook:
    """The hook protocol: a tool call as JSON on stdin; exit 0 allows it, exit 2 denies it with the reason on stderr.
    The roots come from the environment."""

    @pytest.mark.parametrize(
        "stdin", [{"tool_name": "Read", "tool_input": {"file_path": "<ws>/a"}, "cwd": "<ws>"}], indirect=True
    )
    def test_an_allowed_call_exits_0(
        self,
        stdin,
        stderr,
        guard_env,
    ):
        assert guard.hook(stdin, stderr, guard_env) == 0
        assert stderr.getvalue() == ""
        guard_env.paths.assert_has_calls([call("CE_GUARD_READ_ROOTS"), call("CE_GUARD_WRITE_ROOTS")])

    @pytest.mark.parametrize(
        "stdin", [{"tool_name": "Bash", "tool_input": {"command": "env"}, "cwd": "<ws>"}], indirect=True
    )
    def test_a_denied_call_exits_2_with_the_reason_on_stderr(
        self,
        stdin,
        stderr,
        guard_env,
    ):
        assert guard.hook(stdin, stderr, guard_env) == 2
        assert "not available" in stderr.getvalue()


class TestHookFailsClosed:
    """What the hook can't read is denied, and so is everything when no roots are configured."""

    @pytest.mark.parametrize(  # hook inputs that are no tool call the guard can read
        "stdin",
        [
            pytest.param("not json", id="not JSON"),
            pytest.param({"tool_name": "Read", "tool_input": "x"}, id="an input that is not an object"),
            pytest.param([1, 2], id="not an object"),
            pytest.param({"tool_name": ["Read"], "tool_input": {}}, id="a name that is not a string"),
        ],
        indirect=True,
    )
    def test_an_unreadable_input_is_denied(
        self,
        stdin,
        stderr,
        guard_env,
    ):
        assert guard.hook(stdin, stderr, guard_env) == 2
        assert "unreadable hook input" in stderr.getvalue()

    @pytest.mark.parametrize("stdin", [{"tool_name": "Read", "tool_input": {"file_path": "<ws>/a"}}], indirect=True)
    @pytest.mark.parametrize("guard_env", [{"CE_GUARD_READ_ROOTS": [], "CE_GUARD_WRITE_ROOTS": []}], indirect=True)
    def test_without_roots_nothing_is_readable(
        self,
        stdin,
        stderr,
        guard_env,
    ):
        assert guard.hook(stdin, stderr, guard_env) == 2
        assert "outside" in stderr.getvalue()


class TestToolCall:
    """A tool call the CI agent asks to make, as Claude Code hands it to the hook: what is missing reads as empty, and
    malformed JSON is refused."""

    def test_a_null_input_is_empty_and_unknown_keys_are_ignored(
        self,
    ):
        call = guard.ToolCall.model_validate_json('{"tool_name": "Read", "tool_input": null, "session_id": "s"}')
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
            guard.ToolCall.model_validate_json(text)
