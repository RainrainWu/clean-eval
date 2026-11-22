"""Test infrastructure for every layer's tests, and nothing from the package: dates relative to now, throwaway git
repositories, fake `gh` and `claude` executables, files written into a folder, and the command line run in a
subprocess. Classes only: the fixtures that hand them out (`repo`, `fake_bin`, `write_files`, `run_cli`) and the
hermetic git config are in the conftest.py files, and files are read and written with pathlib."""

import datetime as dt
import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = str(
    Path(
        __file__,
    )
    .absolute()
    .parent.parent
)


class Contents:
    """Files' contents as the tests write them into a folder (`write`): a str as it is, None for a file to remove, and
    anything else as JSON."""

    @staticmethod
    def write(
        folder,
        contents,
    ):
        """Write {path in `folder`: content} into `folder`, making folders as needed."""
        for relative, content in contents.items():
            path = Path(
                folder,
                relative,
            )
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2))


class CommandLine:
    """The command line as the skills and the action run it, `python -I <package> <arguments>`, in a subprocess, with the
    package at the path `package` (`run`)."""

    def __init__(
        self,
        package,
    ):
        self.package = package

    def run(
        self,
        *arguments,
        cwd,
        **options,
    ):
        """Run it with `arguments` in the folder `cwd`, and any other keyword of subprocess.run; the CompletedProcess,
        with text output."""
        return subprocess.run(
            [sys.executable, "-I", self.package, *arguments], cwd=cwd, capture_output=True, text=True, **options
        )


class Dates:
    """Times relative to now, for commits and periods that must fall in a window ending today."""

    @staticmethod
    def days_ago(
        days,
    ):
        """Now minus `days` days, UTC, to the second."""
        return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).replace(microsecond=0)


class Repo:
    """A git repository at `path` (made if missing) with dated commits."""

    def __init__(
        self,
        path,
    ):
        self.path = str(path)
        Path(
            self.path,
        ).mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")

    def git(
        self,
        *args,
        env=None,
    ):
        completed = subprocess.run(
            ["git", "-C", self.path, *args], capture_output=True, text=True, env={**os.environ, **(env or {})}
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"git {args}: {completed.stderr}",
            )
        return completed.stdout.strip()

    def write(
        self,
        rel,
        content,
    ):
        full = Path(
            self.path,
            rel,
        )
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def commit(
        self,
        message,
        files=None,
        when=None,
    ):
        """Write `files` ({path: content}), commit everything with `message` at `when` (datetime; default a day ago)."""
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A")
        stamp = (when or Dates.days_ago(1)).isoformat()
        self.git(
            "commit", "-q", "--allow-empty", "-m", message, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
        )
        return self.git("rev-parse", "HEAD")


FAKE_GH = r"""
import json, os, sys
d = os.environ.get("FAKE_GH_DIR") or os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(d, "gh.json")) as f:
    cfg = json.load(f)
args = sys.argv[1:]
stdin = sys.stdin.read() if "--input" in args else ""
with open(os.path.join(d, "gh-calls.jsonl"), "a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin}) + "\n")

def reply(key):
    r = cfg.get(key, {"exit": 0, "out": "{}"})
    if isinstance(r, list):  # successive replies
        n_path = os.path.join(d, f"gh-count-{key}")
        n = 0
        if os.path.exists(n_path):
            with open(n_path) as f:
                n = int(f.read())
        with open(n_path, "w") as f:
            f.write(str(n + 1))
        r = r[min(n, len(r) - 1)]
    out = r.get("out", "")
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
    sys.stderr.write(r.get("err", ""))
    sys.exit(r.get("exit", 0))

if args[:2] == ["auth", "status"]:
    reply("auth")
if args[:2] == ["pr", "view"]:
    reply("pr_view_" + args[2] if "pr_view_" + args[2] in cfg else "pr_view")
if args[0] == "api":
    path = next(a for a in args[1:] if a.startswith("repos/"))
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    if path.endswith("/dismissals"):
        reply("dismiss")
    if path.endswith("/reviews") and method == "POST":
        reply("post_review")
    if path.endswith("/reviews"):
        reply("list_reviews")
    if path.endswith("/comments"):
        reply("inline_comments")
sys.exit(0)
"""

FAKE_CLAUDE = r"""
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(d, "claude.json")) as f:
    plan = json.load(f)
agent = os.environ["CE_GUARD_WRITE_ROOTS"].split(os.pathsep)[0]
with open(os.path.join(d, "claude-invocation.json"), "w") as f:
    json.dump({"argv": sys.argv[1:], "env": sorted(os.environ), "stdin": sys.stdin.read(), "cwd": os.getcwd()}, f)
for rel, content in plan.get("agent_files", {}).items():
    with open(os.path.join(agent, rel), "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))
for rel, content in plan.get("workspace_files", {}).items():
    os.makedirs(os.path.dirname(os.path.join(os.getcwd(), rel)), exist_ok=True)
    with open(os.path.join(os.getcwd(), rel), "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))
print(plan.get("say", "done"))
sys.exit(plan.get("exit", 0))
"""


class FakeBin:
    """A bin folder `path` (made if missing) with fake `gh` and `claude`, configured through JSON files next to them."""

    def __init__(
        self,
        path,
    ):
        self.dir = str(path)
        Path(
            self.dir,
        ).mkdir(parents=True, exist_ok=True)
        self.make_executable(
            Path(
                self.dir,
                "gh",
            ),
            FAKE_GH,
        )
        self.make_executable(
            Path(
                self.dir,
                "claude",
            ),
            FAKE_CLAUDE,
        )
        self.gh({})
        self.claude({})

    @staticmethod
    def make_executable(
        path,
        source,
    ):
        """Write a Python script to `path` that runs with this interpreter, and make it executable."""
        executable = Path(
            path,
        )
        executable.write_text(f"#!{sys.executable}\n" + textwrap.dedent(source))
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def gh(
        self,
        cfg,
    ):
        """Configure the fake gh's replies (see FAKE_GH)."""
        Path(
            self.dir,
            "gh.json",
        ).write_text(json.dumps(cfg))

    def claude(
        self,
        plan,
    ):
        """Configure what the fake claude writes, says and exits with (see FAKE_CLAUDE)."""
        Path(
            self.dir,
            "claude.json",
        ).write_text(json.dumps(plan))

    def gh_calls(
        self,
    ):
        """The fake gh's calls so far: {"args", "stdin"} each, oldest first."""
        calls = Path(
            self.dir,
            "gh-calls.jsonl",
        )
        return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []

    def claude_invocation(
        self,
    ):
        """How the fake claude was run ({"argv", "env", "stdin", "cwd"}), or None when it wasn't."""
        invocation = Path(
            self.dir,
            "claude-invocation.json",
        )
        return json.loads(invocation.read_text()) if invocation.exists() else None

    def env(
        self,
    ):
        """The variables that put the fakes first on PATH."""
        return {"PATH": self.dir + os.pathsep + os.environ["PATH"], "FAKE_GH_DIR": self.dir}
