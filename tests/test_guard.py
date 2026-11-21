import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, write

from clean_eval.external import agent_guard as guard

CE = os.path.join(ROOT, "clean_eval")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp())
        self.ws, self.ctx, self.agent = (os.path.join(self.base, d) for d in ("ws", "ctx", "agent"))
        for d in (self.ws, self.ctx, self.agent, os.path.join(self.ws, ".git")):
            os.makedirs(d)
        write(os.path.join(self.base, "secret.txt"), '')
        os.symlink(os.path.join(self.base, "secret.txt"), os.path.join(self.ws, "link.txt"))
        self.read, self.write = [self.ws, self.ctx], [self.agent]

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def decide(self, tool, **tool_input):
        return guard.decide(tool, tool_input, self.ws, self.read, self.write)

    def test_reads_inside_roots_allowed(self):
        self.assertIsNone(self.decide("Read", file_path=os.path.join(self.ws, "a.py")))
        self.assertIsNone(self.decide("Read", file_path=os.path.join(self.ctx, "diff.patch")))
        self.assertIsNone(self.decide("Read", file_path="relative/b.py"))  # resolved against cwd
        self.assertIsNone(self.decide("Grep", pattern="TODO"))             # defaults to cwd
        self.assertIsNone(self.decide("Glob", pattern="**/*.py", path=self.ws))
        self.assertIsNone(self.decide("Read", file_path=os.path.join(self.agent, "report.md")))

    def test_reads_outside_roots_denied(self):
        self.assertIn("outside", self.decide("Read", file_path="/etc/hosts"))
        self.assertIn("outside", self.decide("Read", file_path=os.path.join(self.ws, "..", "secret.txt")))
        self.assertIn("outside", self.decide("Grep", pattern="KEY", path="/proc/self"))
        self.assertIn("outside", self.decide("Read", file_path=os.path.join(self.ws, "link.txt")))  # symlink escape

    def test_git_dir_denied(self):
        self.assertIn(".git", self.decide("Read", file_path=os.path.join(self.ws, ".git", "config")))
        self.assertIn(".git", self.decide("Grep", pattern="AUTHORIZATION", path=os.path.join(self.ws, ".git")))

    def test_escaping_globs_denied(self):
        self.assertIn("relative", self.decide("Glob", pattern="/proc/*/environ"))
        self.assertIn("relative", self.decide("Glob", pattern="../../*"))
        self.assertIn("relative", self.decide("Glob", pattern="{..,a}/secret.txt"))
        self.assertIn("relative", self.decide("Glob", pattern="a/..x/../../b"))
        self.assertIn("relative", self.decide("Grep", pattern="x", glob="/etc/*"))
        self.assertIn(".git", self.decide("Grep", pattern="AUTHORIZATION", glob=".git/config"))
        self.assertIn(".git", self.decide("Glob", pattern="**/.git/**"))
        self.assertIsNone(self.decide("Grep", pattern="(a|b)", glob="**/*.py"))  # regex patterns are fine

    def test_symlinked_roots_dropped(self):
        link = os.path.join(self.base, "agent-link")
        os.symlink(self.base, link)
        env = {"CE_GUARD_WRITE_ROOTS": os.pathsep.join([link, self.agent])}
        self.assertEqual(guard.roots(env, "CE_GUARD_WRITE_ROOTS"), [self.agent])

    def test_writes_only_in_write_roots(self):
        self.assertIsNone(self.decide("Write", file_path=os.path.join(self.agent, "result.json")))
        self.assertIsNone(self.decide("Edit", file_path=os.path.join(self.agent, "report.md")))
        self.assertIn("outside", self.decide("Write", file_path=os.path.join(self.ws, "clean_eval", "domain", "review.py")))
        self.assertIn("outside", self.decide("Edit", file_path=os.path.join(self.ctx, "risk.json")))
        self.assertIn("missing", self.decide("Write"))

    def test_other_tools_denied(self):
        for tool in ("Bash", "WebFetch", "WebSearch", "Task", "NotebookEdit", "mcp__github__create_issue"):
            self.assertIsNotNone(self.decide(tool, command="echo hi"), tool)
        for tool in ("Skill", "TodoWrite", "ToolSearch"):
            self.assertIsNone(self.decide(tool), tool)

    def run_hook(self, payload, env=None):
        """The hook as Claude Code runs it: `<python> -I <checkout>/clean_eval guard`, from the checkout under review."""
        return subprocess.run([sys.executable, "-I", CE, "guard"], input=payload, capture_output=True, text=True, cwd=self.ws,
                              env={**os.environ, "CE_GUARD_READ_ROOTS": os.pathsep.join(self.read),
                                   "CE_GUARD_WRITE_ROOTS": self.agent, **(env or {})})

    def test_hook_protocol(self):
        ok = self.run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.ws, "a")},
                                       "cwd": self.ws}))
        self.assertEqual(ok.returncode, 0)
        denied = self.run_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "env"}, "cwd": self.ws}))
        self.assertEqual(denied.returncode, 2)
        self.assertIn("not available", denied.stderr)

    def test_hook_fails_closed(self):
        self.assertEqual(self.run_hook("not json").returncode, 2)
        self.assertEqual(self.run_hook(json.dumps({"tool_name": "Read", "tool_input": "x"})).returncode, 2)
        self.assertEqual(self.run_hook(json.dumps([1, 2])).returncode, 2)
        self.assertEqual(self.run_hook(json.dumps({"tool_name": ["Read"], "tool_input": {}})).returncode, 2)
        # no roots configured → nothing is readable
        r = self.run_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": os.path.join(self.ws, "a")}}),
                          env={"CE_GUARD_READ_ROOTS": "", "CE_GUARD_WRITE_ROOTS": ""})
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
