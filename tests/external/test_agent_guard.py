"""The CI agent's PreToolUse guard (external/agent_guard.py): an allowlist that resolves real paths and fails closed."""

import json
import os
import subprocess
import sys

import pytest

from clean_eval.external import agent_guard as guard
from tests.helpers import ROOT, write

CE = os.path.join(ROOT, "clean_eval")


class TestGuard:
    @pytest.fixture(autouse=True)
    def sandbox(self, tmp_path):
        """The agent's folders: the workspace (with a .git and a symlink out of it), the context, the output."""
        self.base = os.path.realpath(tmp_path)
        self.ws, self.ctx, self.agent = (os.path.join(self.base, d) for d in ("ws", "ctx", "agent"))
        for d in (self.ws, self.ctx, self.agent, os.path.join(self.ws, ".git")):
            os.makedirs(d)
        write(os.path.join(self.base, "secret.txt"), "")
        os.symlink(os.path.join(self.base, "secret.txt"), os.path.join(self.ws, "link.txt"))
        self.read, self.write = [self.ws, self.ctx], [self.agent]

    def decide(self, tool, **tool_input):
        return guard.decide(tool, tool_input, self.ws, self.read, self.write)

    def test_reads_inside_roots_allowed(self):
        assert self.decide("Read", file_path=os.path.join(self.ws, "a.py")) is None
        assert self.decide("Read", file_path=os.path.join(self.ctx, "diff.patch")) is None
        assert self.decide("Read", file_path="relative/b.py") is None  # resolved against cwd
        assert self.decide("Grep", pattern="TODO") is None  # defaults to cwd
        assert self.decide("Glob", pattern="**/*.py", path=self.ws) is None
        assert self.decide("Read", file_path=os.path.join(self.agent, "report.md")) is None

    def test_reads_outside_roots_denied(self):
        assert "outside" in self.decide("Read", file_path="/etc/hosts")
        assert "outside" in self.decide("Read", file_path=os.path.join(self.ws, "..", "secret.txt"))
        assert "outside" in self.decide("Grep", pattern="KEY", path="/proc/self")
        assert "outside" in self.decide("Read", file_path=os.path.join(self.ws, "link.txt"))  # symlink escape

    def test_git_dir_denied(self):
        assert ".git" in self.decide("Read", file_path=os.path.join(self.ws, ".git", "config"))
        assert ".git" in self.decide("Grep", pattern="AUTHORIZATION", path=os.path.join(self.ws, ".git"))

    def test_escaping_globs_denied(self):
        assert "relative" in self.decide("Glob", pattern="/proc/*/environ")
        assert "relative" in self.decide("Glob", pattern="../../*")
        assert "relative" in self.decide("Glob", pattern="{..,a}/secret.txt")
        assert "relative" in self.decide("Glob", pattern="a/..x/../../b")
        assert "relative" in self.decide("Grep", pattern="x", glob="/etc/*")
        assert ".git" in self.decide("Grep", pattern="AUTHORIZATION", glob=".git/config")
        assert ".git" in self.decide("Glob", pattern="**/.git/**")
        assert self.decide("Grep", pattern="(a|b)", glob="**/*.py") is None  # regex patterns are fine

    def test_symlinked_roots_dropped(self):
        link = os.path.join(self.base, "agent-link")
        os.symlink(self.base, link)
        assert guard.roots([link, self.agent]) == [self.agent]

    def test_writes_only_in_write_roots(self):
        assert self.decide("Write", file_path=os.path.join(self.agent, "result.json")) is None
        assert self.decide("Edit", file_path=os.path.join(self.agent, "report.md")) is None
        assert "outside" in self.decide("Write", file_path=os.path.join(self.ws, "clean_eval", "domain", "review.py"))
        assert "outside" in self.decide("Edit", file_path=os.path.join(self.ctx, "risk.json"))
        assert "missing" in self.decide("Write")

    @pytest.mark.parametrize(
        "tool", ["Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit", "Skill", "mcp__github__create_issue"]
    )
    def test_every_other_tool_is_denied(self, tool):
        assert self.decide(tool, command="echo hi") is not None

    @pytest.mark.parametrize("tool", ["TodoWrite", "ToolSearch"])
    def test_tools_without_file_access_are_allowed(self, tool):
        assert self.decide(tool) is None

    def run_hook(self, payload, env=None):
        """The hook as Claude Code runs it: `<python> -I <checkout>/clean_eval guard`, from the checkout under review."""
        return subprocess.run(
            [sys.executable, "-I", CE, "guard"],
            input=payload,
            capture_output=True,
            text=True,
            cwd=self.ws,
            env={
                **os.environ,
                "CE_GUARD_READ_ROOTS": os.pathsep.join(self.read),
                "CE_GUARD_WRITE_ROOTS": self.agent,
                **(env or {}),
            },
        )

    def test_hook_protocol(self):
        ok = self.run_hook(
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.ws, "a")}, "cwd": self.ws})
        )
        assert ok.returncode == 0
        denied = self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "env"}, "cwd": self.ws}))
        assert denied.returncode == 2
        assert "not available" in denied.stderr

    def test_hook_fails_closed(self):
        assert self.run_hook("not json").returncode == 2
        assert self.run_hook(json.dumps({"tool_name": "Read", "tool_input": "x"})).returncode == 2
        assert self.run_hook(json.dumps([1, 2])).returncode == 2
        assert self.run_hook(json.dumps({"tool_name": ["Read"], "tool_input": {}})).returncode == 2
        # no roots configured → nothing is readable
        r = self.run_hook(
            json.dumps({"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.ws, "a")}}),
            env={"CE_GUARD_READ_ROOTS": "", "CE_GUARD_WRITE_ROOTS": ""},
        )
        assert r.returncode == 2
