"""Fixtures for the external layer's tests (clean_eval/external/): the real things the gateways wrap, made in temporary
folders (throwaway repositories, files and links, the CI agent's folders, a graph.db), MagicMocks of what the gateways
call out to, and the CI commands' arguments over real folders.

- `git` and `fs` are the real gateways, holding `history` and `files` / `links`. The contract tests parametrize them
  with the in-memory fakes of tests/use_cases/fakes.py too, so each check runs on each.
- `gh` and `graph_build` replace subprocess.run where the GitHub and code-review-graph gateways call it. `host` is the
  CodeHost the command line's cli.github() hands out, and `guard_env` the Environment the guard's hook reads.
- `run_cli` runs `python -I <checkout>/clean_eval ...` in a subprocess, for the end-to-end tests, and `cli_runner`
  runs a command in-process through click, with the CI commands' arguments (argv lists) over real folders.

A fixture reads its customisation from `request.param` and has a default without one. A test sets the param with
`@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test or on its whole class. The fixtures built
from it follow: `repo`, `git` and `shas` from `history`; `fs` from `files` and `links`; the CI commands' arguments from
the data of their run (`tuning`, `eval_history`, `agent_files`, `context_files`, `agent_review`, `risk_report`, ...)."""

import io
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest
from click.testing import CliRunner

from clean_eval.domain.review import RunFolders
from clean_eval.external import cli
from clean_eval.external.claude_code import ClaudeCodeGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.use_cases.evaluation import SNAPSHOT, snapshot_tuning
from clean_eval.use_cases.interfaces import CodeHost, Environment
from clean_eval.use_cases.rules.review import finalize_review, parse_rules
from tests.helpers import ROOT
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit

PACKAGE = os.path.join(
    ROOT, "clean_eval"
)  # what `python -I` runs: the checkout's package, as the skills and the action
# The CI agent's folders a tool call names: "<ws>" in it stands for the sandbox's workspace, and so on.
SANDBOX_FOLDERS = ("base", "ws", "ctx", "agent")


# The process environment.


@pytest.fixture
def environ(
    request,
):
    """Environment variables: {name: value} from `request.param`, a value of None for a variable that must not be set;
    none when not parametrized. `process_env` puts them in this process's environment."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def process_env(
    environ,
    monkeypatch,
):
    """This process's environment (os.environ), with the variables of `environ` set and its None ones unset, for the
    test."""
    for name, value in environ.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    return os.environ


# Repositories: real throwaway ones, and the in-memory fake.


@pytest.fixture
def history(
    request,
):
    """The commits of the repository under test, oldest first: one dict of MemoryGit.commit's arguments per commit
    (`subject`, `files` {path: text}, `when` a datetime, and `remove` [path] or `author` when they matter), from
    `request.param`; none when not parametrized. A real repository's author is always Test (the hermetic git config)."""
    return list(getattr(request, "param", []))


@pytest.fixture
def repo(
    repo,
    history,
):
    """The throwaway repository of tests/conftest.py (a tests.helpers.Repo on branch main), holding the commits of
    `history`."""
    for commit in history:
        for removed in commit.get("remove", ()):
            Path(repo.path, removed).unlink()
        repo.commit(commit["subject"], commit.get("files"), commit.get("when"))
    return repo


@pytest.fixture
def git(
    request,
    history,
):
    """A Git holding the commits of `history`: GitGateway on the throwaway `repo`; or, with `request.param` "MemoryGit",
    the use cases' in-memory fake. The contract tests parametrize it with both ("MemoryGit", "GitGateway")."""
    if getattr(request, "param", "GitGateway") == "MemoryGit":
        fake = MemoryGit()
        for commit in history:
            fake.commit(**commit)
        return fake
    return GitGateway(request.getfixturevalue("repo").path)


@pytest.fixture
def shas(
    request,
    git,
    history,
):
    """The shas of the commits of `history`, oldest first, as `git` holds them."""
    if isinstance(git, MemoryGit):
        return [commit["sha"] for commit in git.commits]
    if not history:
        return []
    return request.getfixturevalue("repo").git("rev-list", "--reverse", "HEAD").split()


# Files and folders: real temporary ones, and the in-memory fake.


@pytest.fixture
def root(
    tmp_path,
):
    """The folder a file-system test works under: a temporary folder, as its real path (no symlink on the way to it,
    so the real file system resolves the paths in it to themselves)."""
    return str(tmp_path.resolve())


@pytest.fixture
def files(
    request,
):
    """The files `fs` starts with: {path under `root`: text}, from `request.param`; none when not parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def links(
    request,
):
    """The symlinks `fs` starts with: {path under `root`: its target under `root`}, from `request.param`; none when not
    parametrized. A target need not exist (a dangling link)."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def fs(
    request,
    root,
    files,
    links,
):
    """A FileSystem holding `files` and `links` under `root`: FileSystemGateway on the real file system; or, with
    `request.param` "MemoryFileSystem", the use cases' in-memory fake. The contract tests parametrize it with both
    ("MemoryFileSystem", "FileSystemGateway")."""
    if getattr(request, "param", "FileSystemGateway") == "MemoryFileSystem":
        fake = MemoryFileSystem()
        for path, text in files.items():
            fake.write_text(str(Path(root, path)), text)
        for path, target in links.items():
            fake.link(str(Path(root, path)), str(Path(root, target)))
        return fake
    for path, text in files.items():
        Path(root, path).parent.mkdir(parents=True, exist_ok=True)
        Path(root, path).write_text(text)
    for path, target in links.items():
        Path(root, path).parent.mkdir(parents=True, exist_ok=True)
        Path(root, path).symlink_to(Path(root, target))
    return FileSystemGateway()


# The CLIs the gateways run, mocked: gh, and code-review-graph's build (with the graph.db it leaves, made for real).


@pytest.fixture
def gh(
    request,
    monkeypatch,
):
    """The `gh` CLI as GitHubGateway runs it: an autospecced MagicMock of subprocess.run, patched into
    clean_eval.external.github only (the module's `subprocess` becomes the real one with `run` mocked, so git and the
    rest of the process still run for real). `request.param` lists its replies to successive calls, each {"out": stdout
    (a str, or anything else as JSON), "err": stderr, "exit": status}, any of them left out ("", "", 0); unparametrized,
    every call succeeds with {}. Each call holds the argv and the stdin (`input`) the gateway passed."""
    run = create_autospec(subprocess.run)
    replies = []
    for reply in getattr(request, "param", []):
        out = reply.get("out", "")
        replies.append(
            subprocess.CompletedProcess(
                ["gh"],
                reply.get("exit", 0),
                out if isinstance(out, str) else json.dumps(out),
                reply.get("err", ""),
            )
        )
    if replies:
        run.side_effect = replies
    else:
        run.return_value = subprocess.CompletedProcess(["gh"], 0, "{}", "")
    monkeypatch.setattr("clean_eval.external.github.subprocess", SimpleNamespace(**{**vars(subprocess), "run": run}))
    return run


@pytest.fixture
def graph_dir(
    tmp_path,
):
    """The data folder of a code-review-graph build, where its graph.db goes: a temporary folder, as its real path."""
    return str(tmp_path.resolve())


@pytest.fixture
def graph_db(
    request,
    graph_dir,
):
    """The graph.db a code-review-graph build leaves in `graph_dir`, made for real (in-process, with sqlite3) by the
    SQL script in `request.param`; no database when not parametrized, or for None. Its path."""
    path = Path(graph_dir, "graph.db")
    script = getattr(request, "param", None)
    if script is not None:
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(script)
            connection.commit()
    return str(path)


@pytest.fixture
def graph_build(
    request,
    monkeypatch,
):
    """The code-review-graph CLI's build as CodeReviewGraphGateway runs it: an autospecced MagicMock of subprocess.run,
    patched into clean_eval.external.code_review_graph only (as `gh` is into the GitHub gateway), that succeeds and
    writes nothing (`graph_db` stands for what a build leaves). `request.param` configures it (configure_mock's
    keywords): a failing build is `{"side_effect": subprocess.CalledProcessError(1, "build", stderr="boom")}`, a
    missing CLI a FileNotFoundError."""
    run = create_autospec(subprocess.run)
    run.return_value = subprocess.CompletedProcess(["code-review-graph"], 0, "", "")
    run.mock.configure_mock(**getattr(request, "param", {}))
    monkeypatch.setattr(
        "clean_eval.external.code_review_graph.subprocess", SimpleNamespace(**{**vars(subprocess), "run": run})
    )
    return run


# The CI agent's guard: its folders (real, since resolving real paths is what it guards with), the tool call it
# decides, the hook's stdin and stderr, and the environment it reads its roots from.


@pytest.fixture
def sandbox(
    tmp_path,
):
    """The CI agent's folders on the real file system, as a CI run lays them out in one folder `base` (resolved): the
    workspace `ws` (the checkout under review, with a .git folder, and link.txt, a symlink out of it to secret.txt in
    `base`), the context `ctx` and the agent's output `agent`. A SimpleNamespace of their paths, and of the roots the
    guard is given: `read_roots` [ws, ctx] and `write_roots` [agent]."""
    base = tmp_path.resolve()
    for folder in ("ws/.git", "ctx", "agent"):
        Path(base, folder).mkdir(parents=True)
    Path(base, "secret.txt").write_text("")
    Path(base, "ws", "link.txt").symlink_to(Path(base, "secret.txt"))
    return SimpleNamespace(
        base=str(base),
        ws=str(base / "ws"),
        ctx=str(base / "ctx"),
        agent=str(base / "agent"),
        read_roots=[str(base / "ws"), str(base / "ctx")],
        write_roots=[str(base / "agent")],
    )


@pytest.fixture
def tool_input(
    request,
    sandbox,
):
    """The input of a tool call the guard decides: `request.param` ({key: value}), where "<base>", "<ws>", "<ctx>" and
    "<agent>" stand for the `sandbox` folders; {} when not parametrized."""
    text = json.dumps(getattr(request, "param", {}))
    for name in SANDBOX_FOLDERS:
        text = text.replace("<%s>" % name, getattr(sandbox, name))
    return json.loads(text)


@pytest.fixture
def stdin(
    request,
    sandbox,
):
    """The hook's stdin, in memory: `request.param`, a tool call as Claude Code hands it (JSON-able data, written as
    JSON; or a str, written as is), where "<base>", "<ws>", "<ctx>" and "<agent>" stand for the `sandbox` folders;
    empty when not parametrized."""
    param = getattr(request, "param", "")
    text = param if isinstance(param, str) else json.dumps(param)
    for name in SANDBOX_FOLDERS:
        text = text.replace("<%s>" % name, getattr(sandbox, name))
    return io.StringIO(text)


@pytest.fixture
def stderr():
    """The hook's stderr, in memory (read it with getvalue())."""
    return io.StringIO()


@pytest.fixture
def guard_env(
    request,
    sandbox,
):
    """The Environment the hook reads its roots from: an autospecced mock whose paths() gives the `sandbox` folders
    a CI run sets, ws and ctx for CE_GUARD_READ_ROOTS and agent for CE_GUARD_WRITE_ROOTS. `request.param` replaces
    a variable's folders, by name: {"CE_GUARD_READ_ROOTS": []} for no read roots."""
    roots = {"CE_GUARD_READ_ROOTS": ["ws", "ctx"], "CE_GUARD_WRITE_ROOTS": ["agent"], **getattr(request, "param", {})}
    mock = create_autospec(Environment, instance=True)
    mock.paths.side_effect = lambda name: [getattr(sandbox, folder) for folder in roots.get(name, [])]
    return mock


# The command line: run in a subprocess, or driven in-process with its code host mocked.


@pytest.fixture
def cli_runner():
    """Runs the command line in-process, through click: `cli_runner.invoke(cli.clean_eval, args)` returns the result,
    with its `exit_code` and what the command printed (`stdout`, `stderr`). An exception the command line doesn't turn
    into an exit status propagates, so a test fails on it with its traceback."""
    return CliRunner(catch_exceptions=False)


@pytest.fixture
def run_cli():
    """Runs the command line as the skills and the action do, `python -I <checkout>/clean_eval <arguments>`, in a
    subprocess: call it with the arguments and the folder to run in (`cwd`), and any other keyword of subprocess.run;
    it returns the CompletedProcess, with text output."""

    def run(
        *arguments,
        cwd,
        **options,
    ):
        return subprocess.run(
            [sys.executable, "-I", PACKAGE, *arguments], cwd=cwd, capture_output=True, text=True, **options
        )

    return run


@pytest.fixture
def host(
    request,
    monkeypatch,
):
    """The code host behind the command line: an autospecced CodeHost, which cli.github() hands out for the test.
    `request.param` configures it (configure_mock's keywords), e.g. `{"post_review.side_effect": RemoteError("...")}`;
    unconfigured, its methods return MagicMocks."""
    mock = create_autospec(CodeHost, instance=True)
    mock.configure_mock(**getattr(request, "param", {}))
    monkeypatch.setattr(cli, "github", lambda: mock)
    return mock


@pytest.fixture
def write_files():
    """Writes files into a folder: call it with the folder and {path in it: content}. A str is written as is, None
    removes the file, and anything else is written as JSON; folders are made as needed."""

    def write(
        folder,
        contents,
    ):
        for relative, content in contents.items():
            path = Path(folder, relative)
            if content is None:
                path.unlink()
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2))

    return write


@pytest.fixture
def ci_folders(
    tmp_path,
):
    """The folders of a CI run, as the action lays them out (under tmp_path): `ctx`, the context `prepare` precomputes;
    `agent`, the agent's output; `out`, the results CI trusts; `ws`, the workspace (the checkout); and `gh_output`, the
    step-outputs file ($GITHUB_OUTPUT). A SimpleNamespace of their paths: ctx, agent and ws exist, out and gh_output
    are the commands' to write."""
    folders = SimpleNamespace(
        ctx=str(tmp_path / "ctx"),
        agent=str(tmp_path / "agent"),
        out=str(tmp_path / "out"),
        ws=str(tmp_path / "ws"),
        gh_output=str(tmp_path / "gh_output"),
    )
    for folder in (folders.ctx, folders.agent, folders.ws):
        Path(folder).mkdir()
    return folders


@pytest.fixture
def agent_files(
    request,
):
    """What the CI agent wrote to its output folder: {name: content} from `request.param` (a str is written as is,
    anything else as JSON); a result.json that proposes nothing ({}) when not parametrized."""
    return dict(getattr(request, "param", {"result.json": {}}))


@pytest.fixture
def context_files(
    request,
):
    """More files in the CI run's context folder: {path in it: content} from `request.param` (a str is written as is,
    anything else as JSON), written after the ones `prepare` writes, so one can replace them
    (`{"eval-history.json": "{broken"}`); none when not parametrized."""
    return dict(getattr(request, "param", {}))


# The data of a PR review: the rules, what the agent proposed, the change's risk, and the review CI trusts.


@pytest.fixture
def review(
    agent_review,
    risk_report,
    review_rules,
):
    """The Review CI trusts, as `finalize` writes it to result.json: `agent_review` finalized against `risk_report` and
    `review_rules`."""
    return finalize_review(agent_review, risk_report, parse_rules(review_rules))


# The data of an eval: the merged-PR history, and the repository's tuning files before and after the agent ran.


@pytest.fixture
def eval_history(
    request,
    facts,
):
    """An eval-history.json as `history` collects it (a dict): `hot` defective PRs on widely depended-on modules hot0,
    hot1, ... and `cold` clean PRs on leaf modules cold0, ..., each module with `facts`; (hot, cold) from
    `request.param`, (6, 16) by default. Under the default policy every hot PR outscores every cold one: AUC 1.0, and
    undefined without both kinds."""
    hot, cold = getattr(request, "param", (6, 16))
    prs = []
    for number in range(hot + cold):
        defective = number < hot
        name = "hot%d" % number if defective else "cold%d" % (number - hot)
        defect = {
            "fix_sha": "%040x" % (1000 + number),
            "subject": "fix",
            "kind": "fix",
            "confidence": "heuristic",
            "days_after": 1.0,
            "overlap_files": [name + ".py"],
        }
        module_facts = facts.model_copy(update={"name": name, "fan_in": 6 if defective else 0})
        prs.append(
            {
                "sha": "%040x" % number,
                "base": "%040x" % (500 + number),
                "author": "dev",
                "ts": number,
                "subject": "pr",
                "pr": number,
                "max_tier": "Moderate",
                "max_score": 0,
                "defects": [defect] if defective else [],
                "modules": [
                    {
                        "module": name,
                        "band": "Moderate",
                        "score": 0,
                        "rated": True,
                        "facts": module_facts.model_dump(mode="json"),
                    }
                ],
            }
        )
    return {
        "period": {
            "since": "2026-01-01",
            "until": "2026-02-01",
            "branch": "main",
            "defect_window_days": 30,
            "policy_version": 2,
        },
        "totals": {"prs": hot + cold, "defective_prs": hot},
        "calibration": {"auc_max_score": 1.0 if hot and cold else None},
        "prs": prs,
    }


@pytest.fixture
def tuning(
    request,
):
    """The repository's .clean-eval/ before the CI agent ran (what `prepare eval` snapshots): {path in it: content}
    from `request.param` (a str is written as is, anything else as JSON); an empty folder when not parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def tuning_edits(
    request,
):
    """What the CI agent did to .clean-eval/: {path in it: its new content, or None for a file it removed} from
    `request.param` (a str is written as is, anything else as JSON); nothing when not parametrized."""
    return dict(getattr(request, "param", {}))


# The CI commands' arguments (argv lists for `cli_runner`), over the folders their steps leave.


@pytest.fixture
def finalize_eval_args(
    request,
    ci_folders,
    tuning,
    tuning_edits,
    eval_history,
    context_files,
    agent_files,
    write_files,
):
    """The arguments of `finalize --skill eval` over `ci_folders`, as `prepare eval` and the agent leave them: the
    workspace's .clean-eval/ held `tuning` when it was snapshotted into the context, then the agent made `tuning_edits`
    and wrote `agent_files`; the context holds `eval_history` and `context_files`. `request.param` is the options after
    the folders: ["--apply"] by default ([] for a run without it)."""
    tuning_dir = Path(ci_folders.ws, ".clean-eval")
    tuning_dir.mkdir()
    write_files(tuning_dir, tuning)
    snapshot_tuning(FileSystemGateway(), ci_folders.ws, str(Path(ci_folders.ctx, SNAPSHOT)))
    write_files(tuning_dir, tuning_edits)
    write_files(ci_folders.ctx, {"eval-history.json": eval_history, **context_files})
    write_files(ci_folders.agent, agent_files)
    return [
        "finalize",
        "--skill",
        "eval",
        "--ctx",
        ci_folders.ctx,
        "--agent",
        ci_folders.agent,
        "--out",
        ci_folders.out,
        "--workspace",
        ci_folders.ws,
        "--gh-output",
        ci_folders.gh_output,
        *getattr(request, "param", ["--apply"]),
    ]


@pytest.fixture
def finalize_review_args(
    ci_folders,
    risk_report,
    review_rules,
    context_files,
    agent_files,
    write_files,
):
    """The arguments of `finalize --skill pr-review` over `ci_folders`, as `prepare pr-review` and the agent leave
    them: the context holds `risk_report` (risk.json), `review_rules` (review-rules.md) and `context_files`, and the
    agent wrote `agent_files`."""
    write_files(
        ci_folders.ctx,
        {"risk.json": risk_report.model_dump(mode="json"), "review-rules.md": review_rules, **context_files},
    )
    write_files(ci_folders.agent, agent_files)
    return [
        "finalize",
        "--skill",
        "pr-review",
        "--ctx",
        ci_folders.ctx,
        "--agent",
        ci_folders.agent,
        "--out",
        ci_folders.out,
        "--gh-output",
        ci_folders.gh_output,
    ]


@pytest.fixture
def post_args(
    ci_folders,
    risk_report,
    review,
    context_files,
    write_files,
):
    """The arguments of `post` for the PR and head commit of `risk_report`, over `ci_folders` as `finalize` leaves them:
    the results folder holds `review` (result.json) and its report.md. With `context_files` (the diff `prepare
    pr-review` writes: diff/index.json and a patch per file) the context holds them and the command gets --ctx, as the
    action passes it; without them it runs without --ctx."""
    write_files(ci_folders.out, {"result.json": review.model_dump(mode="json"), "report.md": "## Review\n"})
    write_files(ci_folders.ctx, context_files)
    context = ["--ctx", ci_folders.ctx] if context_files else []
    return ["post", "--pr", str(risk_report.pr), "--out", ci_folders.out, "--head", risk_report.head, *context]


# Claude Code, the CI agent: its gateway over real folders, with its CLI and npm mocked.

# What the agent's process may see of the runner's environment, and what it must not.
AGENT_VARIABLES = {
    "PATH": "/usr/bin:/bin",
    "ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 40,
    "GH_TOKEN": "ghs_" + "t" * 36,
    "SOME_RUNNER_TOKEN": "x" * 20,
}
GUARD = "python -I /action/clean_eval guard || exit 2"  # the PreToolUse hook the composition root passes


@pytest.fixture
def claude_runs(
    request,
    monkeypatch,
):
    """subprocess.run as the Claude Code gateway calls it (npm, and claude itself): an autospecced MagicMock patched into
    clean_eval.external.claude_code only. Every call finishes with `request.param`: {"exit": status, "out": what it
    printed, "err": its stderr} (by default 0, "done", ""), or raises it when it is an exception."""
    run = create_autospec(subprocess.run)
    reply = getattr(request, "param", {})
    if isinstance(reply, BaseException):
        run.side_effect = reply
    else:
        run.return_value = subprocess.CompletedProcess(
            ["claude"], reply.get("exit", 0), reply.get("out", "done"), reply.get("err", "")
        )
    monkeypatch.setattr(
        "clean_eval.external.claude_code.subprocess", SimpleNamespace(**{**vars(subprocess), "run": run})
    )
    return run


@pytest.fixture
def claude_installed(
    request,
    monkeypatch,
):
    """Whether `claude` is on the PATH the gateway looks on: `request.param`, True by default. shutil.which is mocked in
    clean_eval.external.claude_code only; the mock is returned."""
    which = MagicMock(return_value="/usr/bin/claude" if getattr(request, "param", True) else None)
    monkeypatch.setattr("clean_eval.external.claude_code.shutil", SimpleNamespace(which=which))
    return which


@pytest.fixture
def run_folders(
    tmp_path,
):
    """The folders of a CI run, made under tmp_path (resolved): the workspace, and the output folder with the context
    and the agent's folder in it."""
    base = tmp_path.resolve()
    folders = RunFolders(
        workspace=str(base / "ws"),
        out=str(base / "out"),
        context=str(base / "out" / "context"),
        agent=str(base / "out" / "agent"),
    )
    for folder in (folders.workspace, folders.context, folders.agent):
        Path(folder).mkdir(parents=True)
    return folders


@pytest.fixture
def claude_code(
    request,
    run_folders,
    claude_runs,
    claude_installed,
):
    """The Claude Code gateway of a run in `run_folders`, writing its settings with FileSystemGateway, with
    `claude_runs` and `claude_installed` in place of the CLI and the PATH. `request.param` replaces any of its
    constructor's keywords: `variables` (AGENT_VARIABLES by default), `version`, `model`, `max_turns`."""
    keywords: dict[str, Any] = {
        "variables": AGENT_VARIABLES,
        "guard_command": GUARD,
        "install_dir": run_folders.out,
        **getattr(request, "param", {}),
    }
    return ClaudeCodeGateway(FileSystemGateway(), **keywords)
