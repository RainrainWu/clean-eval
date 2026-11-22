"""Support for the end-to-end tests of the GitHub Action (tests/test_action.py): its command, `clean_eval action`, run as
action.yml runs it on a GitHub runner (its step's `run:` line, by bash), with fake `gh` and `claude`
(tests.helpers.FakeBin) and fake credentials. Classes and constants only; the fixtures that hand them out (`action`,
`action_run`) are in tests/conftest.py."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from tests.helpers import ROOT

PACKAGE = (
    Path(
        ROOT,
    )
    / "clean_eval"
)
# The command action.yml's last step runs: its `run:` line, which bash runs with the step's environment.
RUN_LINE = re.findall(
    r"^ +run: '(.+)'$",
    (
        Path(
            ROOT,
        )
        / "action.yml"
    ).read_text(),
    re.MULTILINE,
)[-1]
# The variables of the machine running the tests that a run must not inherit: the action's, the runner's, Claude's.
NOT_INHERITED = ("CE_", "GITHUB_", "CLAUDE", "ANTHROPIC_", "ACTIONS_", "RUNNER_")
FAKE_KEY = "sk-ant-api03-" + "k" * 40  # the Anthropic API key the agent may see
GH_TOKEN = "ghs_" + "t" * 36  # the GitHub token the agent must never see, and no report may leak
RUNNER_TOKEN = "x" * 20  # some other secret of the runner's environment


class Action:
    """The action's command on `workspace` (a tests.helpers.Repo), run as action.yml runs it on a GitHub runner: the action's inputs as CE_*
    variables, fake credentials, the fake `gh` and `claude` of `fake_bin` first on PATH, and the runner's files in
    `folder`: the output folder `out`, the step outputs `gh_out` and the step summary `summary`.

    Every run first configures the fakes: `claude` with `claude_plan`, and `gh` with `gh_replies` plus `pull_request`
    under `pr_view`, its base and head being the workspace's main and HEAD as they are then."""

    def __init__(
        self,
        folder,
        fake_bin,
        workspace,
        gh_replies,
        claude_plan,
        pull_request,
    ):
        self.folder = Path(
            folder,
        )
        self.out = self.folder / "out"
        self.gh_out = self.folder / "gh_out"
        self.summary = self.folder / "summary.md"
        self.fake_bin = fake_bin
        self.workspace = workspace
        self.gh_replies = gh_replies
        self.claude_plan = claude_plan
        self.pull_request = pull_request

    def configure_fakes(
        self,
    ):
        """Tell the fake `claude` what to do, and the fake `gh` what to answer (see FakeBin)."""
        self.fake_bin.claude(self.claude_plan)
        shown = {
            **self.pull_request,
            "baseRefOid": self.workspace.git("rev-parse", "main"),
            "headRefOid": self.workspace.git("rev-parse", "HEAD"),
        }
        self.fake_bin.gh({"pr_view": {"out": shown}, **self.gh_replies})

    def run(
        self,
        checkout=None,
        **inputs,
    ):
        """Run the action's command in `checkout` (by default the workspace) with `inputs` as the action's inputs
        (`fail_on="blocker"` sets CE_FAIL_ON), as action.yml's step runs it: its `run:` line, by bash, with
        clean-eval's Python and its checkout as the action's; return the finished process."""
        self.configure_fakes()
        where = str(checkout or self.workspace.path)
        env = {name: value for name, value in os.environ.items() if not name.startswith(NOT_INHERITED)}
        env.update(self.fake_bin.env())
        env.update(
            {
                "CE_PYTHON": sys.executable,
                "GITHUB_ACTION_PATH": str(ROOT),
                "CE_WORKSPACE": where,
                "CE_OUTPUT_DIR": str(self.out),
                "GITHUB_OUTPUT": str(self.gh_out),
                "GITHUB_STEP_SUMMARY": str(self.summary),
                "ANTHROPIC_API_KEY": FAKE_KEY,
                "GH_TOKEN": GH_TOKEN,
                "SOME_RUNNER_TOKEN": RUNNER_TOKEN,
            }
        )
        env.update({"CE_" + name.upper(): value for name, value in inputs.items()})
        self.gh_out.write_text("")
        return subprocess.run(["bash", "-c", RUN_LINE], capture_output=True, text=True, env=env, cwd=where)

    def outputs(
        self,
    ):
        """The step outputs of the last run: {name: value}."""
        return dict(line.split("=", 1) for line in self.gh_out.read_text().splitlines() if "=" in line)

    def run_hook(
        self,
        hook,
        tool,
        **tool_input,
    ):
        """Run the PreToolUse `hook` command as Claude Code runs it, by a shell in the workspace, on a call of `tool`
        with `tool_input`; the guard may read the workspace and write the agent's folder. Return the finished
        process: status 0 allows the call, 2 denies it."""
        env = {
            "PATH": os.environ["PATH"],
            "CE_GUARD_READ_ROOTS": self.workspace.path,
            "CE_GUARD_WRITE_ROOTS": str(self.out / "agent"),
        }
        payload = json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": self.workspace.path})
        return subprocess.run(
            ["sh", "-c", hook], input=payload, capture_output=True, text=True, env=env, cwd=self.workspace.path
        )
