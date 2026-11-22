"""Fixtures for the external layer's tests (clean_eval/external/): the real things the gateways wrap, made in temporary
folders (throwaway repositories, files and links, the CI agent's folders), MagicMocks of what the gateways
call out to, and the CI commands' arguments over real folders.

- `git` and `fs` are the real gateways, holding `history` and `files` / `links`. The contract tests parametrize them
  with the in-memory fakes of tests/use_cases/fakes.py too, so each check runs on each.
- `gh` replaces subprocess.run where the GitHub gateway calls it. For the Claude Code gateway, `claude_runs` replaces
  subprocess.Popen (claude), `npm_installs` subprocess.run (npm) and `oidc_tokens` urllib's urlopen (the job's OIDC
  token). `host` is the CodeHost the command line's cli.github() hands out, and `guard_env` the Environment the
  guard's hook reads.
- `run_cli` runs `python -I <checkout>/clean_eval ...` in a subprocess, for the end-to-end tests, and `cli_runner`
  runs a command in-process through click, with the CI commands' arguments (argv lists) over real folders.

A fixture reads its customisation from `request.param` and has a default without one. A test sets the param with
`@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test or on its whole class. The fixtures built
from it follow: `repo`, `git` and `shas` from `history`; `fs` from `files` and `links`; the CI commands' arguments from
the data of their run (`tuning`, `eval_history`, `agent_files`, `context_files`, `agent_review`, `risk_report`, ...)."""

import datetime as dt
import io
import json
import os
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import (
    MagicMock,
    create_autospec,
)

import pytest
from click.testing import CliRunner

from clean_eval.domain.review import (
    HostReview,
    InlineComment,
    ReviewPost,
    RunFolders,
)
from clean_eval.external import cli
from clean_eval.external.claude_code import ClaudeCodeGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.use_cases.evaluation import (
    SNAPSHOT,
    snapshot_tuning,
)
from clean_eval.use_cases.interfaces import (
    CodeHost,
    Environment,
)
from clean_eval.use_cases.rules.review import (
    finalize_review,
    parse_rules,
)
from tests.external.fakes import (
    ClaudeProcess,
    TokenEndpoint,
)
from tests.helpers import (
    ROOT,
    CommandLine,
    Contents,
    Dates,
)
from tests.use_cases.fakes import (
    MemoryFileSystem,
    MemoryGit,
)

PACKAGE = os.path.join(
    ROOT, "clean_eval"
)  # what `python -I` runs: the checkout's package, as the skills and the action
# The CI agent's folders a tool call names: "<ws>" in it stands for the sandbox's workspace, and so on.
SANDBOX_FOLDERS = ("base", "ws", "ctx", "agent")


# The process environment.


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


@pytest.fixture
def variables_of_each_kind():
    """Variables of each kind the environment gateway reads ({name: value}, for `environ`): a path list with an empty
    entry (CE_ROOTS), an empty one (CE_EMPTY, how the guard gets no roots), a plain one (CE_X) and one that is not set
    (CE_UNSET)."""
    return {"CE_ROOTS": os.pathsep.join(["/a", "", "/b c"]), "CE_EMPTY": "", "CE_X": "1", "CE_UNSET": None}


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
            Path(
                repo.path,
                removed,
            ).unlink()
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
    return GitGateway(
        request.getfixturevalue("repo").path,
    )


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


# The histories the git gateway's tests give `history` (test_git.py).


@pytest.fixture
def odd_names_history():
    """A commit of a.py two days ago, then one a day ago that adds files whose names need quoting: src/résumé.py and
    src/we"ird.py."""
    return [
        {"subject": "init", "files": {"a.py": "1\n"}, "when": Dates.days_ago(2)},
        {"subject": "odd names", "files": {"src/résumé.py": "2\n", 'src/we"ird.py': "3\n"}, "when": Dates.days_ago(1)},
    ]


@pytest.fixture
def merged_pr_history():
    """A commit of a.py three days ago, then the merge of PR #2 two days ago, which adds b.py."""
    return [
        {"subject": "init", "files": {"a.py": "1\n"}, "when": Dates.days_ago(3)},
        {"subject": "feat: b (#2)", "files": {"b.py": "2\n"}, "when": Dates.days_ago(2)},
    ]


@pytest.fixture
def contract_history():
    """The history of the contract checks, both commits by Test at noon UTC: src/a.py and docs/n.md on 2026-03-01; then,
    on 2026-03-02, the merge of PR #2, which changes src/a.py, adds src/b.py and removes docs/n.md."""
    return [
        {
            "subject": "init",
            "files": {"src/a.py": "a\n", "docs/n.md": "n\n"},
            "when": dt.datetime(2026, 3, 1, 12, tzinfo=dt.timezone.utc),
            "author": "Test",
        },
        {
            "subject": "feat: b (#2)",
            "files": {"src/a.py": "a2\n", "src/b.py": "b\n"},
            "when": dt.datetime(2026, 3, 2, 12, tzinfo=dt.timezone.utc),
            "author": "Test",
            "remove": ["docs/n.md"],
        },
    ]


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
            fake.write_text(os.path.join(root, path), text)
        for path, target in links.items():
            fake.link(os.path.join(root, path), os.path.join(root, target))
        return fake
    Contents.write(root, files)
    for path, target in links.items():
        link = Path(
            root,
            path,
        )
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(os.path.join(root, target))
    return FileSystemGateway()


# The CLI the GitHub gateway runs, mocked.


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
        run.return_value = subprocess.CompletedProcess(
            ["gh"],
            0,
            "{}",
            "",
        )
    monkeypatch.setattr(
        "clean_eval.external.github.subprocess",
        SimpleNamespace(
            **{**vars(subprocess), "run": run},
        ),
    )
    return run


# What gh prints to the GitHub gateway, and the reviews the gateway posts (test_github.py).


@pytest.fixture
def review_threads_view():
    """A PR's reviews and comments as `gh pr view --json reviews,comments,labels,additions,deletions,url` prints them
    (a dict): none of either, no label, a line added and one removed, at url "u"."""
    return {"url": "u", "reviews": [], "comments": [], "labels": [], "additions": 1, "deletions": 1}


@pytest.fixture
def inline_comment_pages():
    """A PR's inline comments as `gh api --paginate` prints them: two pages, one JSON document each, holding "x" by a
    and then "y" by b."""
    return '[{"user":{"login":"a"},"body":"x"}][{"user":{"login":"b"},"body":"y"}]'


@pytest.fixture
def review_pages():
    """A PR's reviews as `gh api --paginate` prints them: two pages, one JSON document each, holding review 10 (a change
    request by github-actions[bot]), an entry not readable as a review (never one of ours to dismiss) and review 11 (a
    comment with no user and no body)."""
    return (
        '[{"id": 10, "state": "CHANGES_REQUESTED", "user": {"login": "github-actions[bot]"}, "body": "m"}]'
        '[{"id": "x"}, {"id": 11, "state": "COMMENTED", "user": null, "body": null}]'
    )


@pytest.fixture
def review_with_comments():
    """A review as the GitHub gateway posts it (a ReviewPost): a change request on commit "c" * 40, with one inline
    comment, a CA-01 blocker on line 2 of a.py."""
    return ReviewPost(
        commit_id="c" * 40,
        body="b",
        event="REQUEST_CHANGES",
        comments=[
            InlineComment(
                path="a.py",
                line=2,
                side="RIGHT",
                body="[CA-01][blocker] s",
            )
        ],
    )


@pytest.fixture
def review_without_comments(
    review_with_comments,
):
    """`review_with_comments` as a plain comment, without inline comments (`comments` left at its default)."""
    return ReviewPost(
        commit_id=review_with_comments.commit_id,
        body=review_with_comments.body,
        event="COMMENT",
    )


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
        Path(
            base,
            folder,
        ).mkdir(parents=True)
    Path(
        base,
        "secret.txt",
    ).write_text("")
    Path(
        base,
        "ws",
        "link.txt",
    ).symlink_to(
        Path(
            base,
            "secret.txt",
        )
    )
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
    return io.StringIO(
        text,
    )


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
    return CliRunner(
        catch_exceptions=False,
    )


@pytest.fixture
def run_cli():
    """Runs the command line as the skills and the action do, `python -I <checkout>/clean_eval <arguments>`, in a
    subprocess: call it with the arguments and the folder to run in (`cwd`), and any other keyword of subprocess.run;
    it returns the CompletedProcess, with text output (tests.helpers.CommandLine, on this checkout's package)."""
    return CommandLine(
        PACKAGE,
    ).run


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
    removes the file, and anything else is written as JSON; folders are made as needed (tests.helpers.Contents)."""
    return Contents.write


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
        Path(
            folder,
        ).mkdir()
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


# The command line end to end (test_cli.py): the commands it has, and the repositories it runs on, as histories for
# `history`.


@pytest.fixture
def cli_commands():
    """The commands of the command line, in the order its usage (`clean_eval --help`) lists them."""
    return ["action", "assess", "finalize", "guard", "history", "post", "prepare", "rate", "readme", "redact", "report"]


@pytest.fixture
def billing_history():
    """A billing module (one with tests) and a users module that depends on it, four days ago; then a change of the users
    module two days ago, which also adds docs and infrastructure."""
    return [
        {
            "subject": "init",
            "files": {
                "src/billing/core.py": "import requests\n\ndef charge(x):\n    return requests.post('u', json=x)\n"
                + "\n".join("def f%d(a):\n    return a\n" % number for number in range(60)),
                "src/billing/models.py": "class Invoice:\n    pass\n" * 40,
                "src/users/api.py": (
                    "from src.billing.core import charge\n\ndef sign_up(u):\n    return charge(u)\n" * 30
                ),
                "tests/test_core.py": "from src.billing.core import charge\n\ndef test_charge():\n    assert charge\n",
            },
            "when": Dates.days_ago(4),
        },
        {
            "subject": "feat: users",
            "files": {
                "src/users/api.py": "def sign_up(u):\n    return u\n",
                "docs/x.md": "doc\n",
                "infra/main.tf": "resource {}\n",
            },
            "when": Dates.days_ago(2),
        },
    ]


@pytest.fixture
def app_with_readme_history():
    """An app module and a README, committed two days ago."""
    return [
        {
            "subject": "init",
            "files": {"app/a.py": "def a():\n    return 1\n", "README.md": "# App\n"},
            "when": Dates.days_ago(2),
        }
    ]


@pytest.fixture
def v1_policy_history():
    """A module and a .clean-eval/risk-model.json of the v1 model, which doesn't validate, committed a day ago."""
    return [
        {
            "subject": "init",
            "files": {"a/b.py": "x = 1\n", ".clean-eval/risk-model.json": '{"window_days": 30}'},
            "when": Dates.days_ago(1),
        }
    ]


@pytest.fixture
def fixed_feature_history():
    """A module made on 2026-01-01; then a feature PR (#1) changes it on 2026-02-01, and a fix of it (#2) the next day,
    each at noon UTC."""
    return [
        {
            "subject": "init",
            "files": {"src/a/x.py": "1\n"},
            "when": dt.datetime(2026, 1, 1, 12, tzinfo=dt.timezone.utc),
        },
        {
            "subject": "feat: a (#1)",
            "files": {"src/a/x.py": "2\n"},
            "when": dt.datetime(2026, 2, 1, 12, tzinfo=dt.timezone.utc),
        },
        {
            "subject": "fix: a (#2)",
            "files": {"src/a/x.py": "3\n"},
            "when": dt.datetime(2026, 2, 2, 12, tzinfo=dt.timezone.utc),
        },
    ]


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


# What `finalize --skill pr-review` and `post` are given (test_cli.py): the agent's findings and report, the diff in the
# context, and the reviews already on the PR.


@pytest.fixture
def blocker_finding(
    finding,
):
    """`finding` as a blocker of rule CA-01 (on line 3 of a.py)."""
    return {**finding, "rule": "CA-01", "severity": "blocker"}


@pytest.fixture
def unplaced_finding(
    finding,
):
    """`finding` (a major one of rule Q-02) on no file and no line, so never attached inline."""
    return {**finding, "file": None, "line": None}


@pytest.fixture
def unsanitized_report():
    """A report.md as the review agent may write it, before CI sanitizes it: with a clean-eval marker of its own and a
    GitHub token."""
    return "## Review\n<!-- clean-eval:pr-review tier=Low -->\nghp_" + "x" * 36


@pytest.fixture
def prepared_diff():
    """The diff of a.py as `prepare pr-review` writes it to the context ({path in it: content}, for `context_files`):
    its index and its patch, which changes line 2, so lines 1-3 take inline comments."""
    return {
        "diff/index.json": [
            {
                "file": "a.py",
                "module": None,
                "patch": "diff/001.patch",
                "patch_lines": 8,
                "added": 1,
                "removed": 1,
                "truncated": False,
            }
        ],
        "diff/001.patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n x\n-y\n+Y\n z\n",
    }


@pytest.fixture
def reviews_once_posted():
    """The reviews on the PR (HostReviews, as the code host lists them) once github-actions[bot] has posted review 50:
    its earlier change request (10) is stale, but not a human's (11), its comment (12) or the new review itself."""
    marker = "<!-- clean-eval:pr-review tier=High -->"
    return [
        HostReview(
            id=10,
            state="CHANGES_REQUESTED",
            user="github-actions[bot]",
            body=marker,
        ),
        HostReview(
            id=11,
            state="CHANGES_REQUESTED",
            user="human",
            body=marker,
        ),
        HostReview(
            id=12,
            state="COMMENTED",
            user="github-actions[bot]",
            body=marker,
        ),
        HostReview(
            id=50,
            state="CHANGES_REQUESTED",
            user="github-actions[bot]",
            body=marker,
        ),
    ]


# The data of an eval: the merged-PR history, and the repository's tuning files before and after the agent ran.


@pytest.fixture
def eval_history_prs(
    request,
    facts,
):
    """The merged PRs of `eval_history`, as `history` collects them (dicts): `hot` defective PRs on widely depended-on
    modules hot0, hot1, ... and `cold` clean PRs on leaf modules cold0, ..., each module with `facts`; (hot, cold) from
    `request.param`, (6, 16) by default. Under the default policy every hot PR outscores every cold one."""
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
    return prs


@pytest.fixture
def eval_history(
    eval_history_prs,
):
    """An eval-history.json as `history` collects it (a dict), of `eval_history_prs`: AUC 1.0 with both hot and cold
    PRs, and undefined without both kinds."""
    hot = sum(1 for pr in eval_history_prs if pr["defects"])
    cold = len(eval_history_prs) - hot
    prs = eval_history_prs
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


# What the CI agent writes for an eval, which `finalize --skill eval` judges (test_cli.py): risk-model.json contents (its
# edits, and one that was there before) and a recommendation.


@pytest.fixture
def valid_model_edit():
    """A risk-model.json the CI agent may write (a dict): it validates, and keeps the AUC of `eval_history`."""
    return {"business_weights": {"dependents": 0.4}}


@pytest.fixture
def structural_model_edit():
    """A risk-model.json that changes the model's structure (its depth), which CI may not change."""
    return {"depth": 1}


@pytest.fixture
def flat_model_edit():
    """A risk-model.json that drops the only terms telling the hot modules of `eval_history` from the cold ones, so
    every PR scores the same (AUC 0.5 < 1.0)."""
    return {"business_weights": {"dependents": 0}, "effects_mix": {"dependents": 0}}


@pytest.fixture
def earlier_model():
    """A risk-model.json that was in .clean-eval/ before the CI agent ran."""
    return {"depth": 2, "business_weights": {"dependents": 0.35}}


@pytest.fixture
def recommendation():
    """A recommendation as the eval agent lists it in result.json: a change of risk-model.json, with its evidence."""
    return {"target": "risk-model.json", "change": "c", "evidence": "e"}


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
    tuning_dir = Path(
        ci_folders.ws,
        ".clean-eval",
    )
    tuning_dir.mkdir()
    write_files(tuning_dir, tuning)
    snapshot_tuning(
        FileSystemGateway(),
        ci_folders.ws,
        str(
            Path(
                ci_folders.ctx,
                SNAPSHOT,
            )
        ),
    )
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

GUARD = "python -I /action/clean_eval guard || exit 2"  # the PreToolUse hook the composition root passes


@pytest.fixture
def agent_variables(
    anthropic_api_key,
):
    """A runner's environment as the agent's process could inherit it: what it may see (PATH, the Anthropic API key) and
    what it must not (a GitHub token, another runner token)."""
    return {
        "PATH": "/usr/bin:/bin",
        "ANTHROPIC_API_KEY": anthropic_api_key,
        "GH_TOKEN": "ghs_" + "t" * 36,
        "SOME_RUNNER_TOKEN": "x" * 20,
    }


@pytest.fixture
def ci_without_credentials():
    """A CI runner's variables, without Claude credentials."""
    return {"PATH": "/usr/bin", "GITHUB_ACTIONS": "true"}


@pytest.fixture
def ci_with_a_key(
    ci_without_credentials,
    anthropic_api_key,
):
    """A CI runner's variables, with an Anthropic API key."""
    return {**ci_without_credentials, "ANTHROPIC_API_KEY": anthropic_api_key}


@pytest.fixture
def token_request():
    """What the runner sets for a job with `id-token: write`, to request its OIDC token: the endpoint, and the request's
    bearer token."""
    return {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.actions.example/idtoken?api-version=2.0",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-" + "r" * 32,
    }


@pytest.fixture
def federated_job(
    ci_without_credentials,
    token_request,
):
    """A CI job that signs in keyless, by workload identity federation: the rule, the organization, and the job's means
    to request its OIDC token."""
    return {
        **ci_without_credentials,
        "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_example",
        "ANTHROPIC_ORGANIZATION_ID": "00000000-0000-4000-8000-000000000000",
        **token_request,
    }


@pytest.fixture
def federated_job_without_id_token(
    federated_job,
):
    """`federated_job` without `id-token: write`: the runner sets no means to request the OIDC token (None: unset)."""
    return {**federated_job, "ACTIONS_ID_TOKEN_REQUEST_URL": None, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": None}


@pytest.fixture
def npm_installs(
    request,
):
    """subprocess.run as the Claude Code gateway calls it to install the CLI with npm: an autospecced MagicMock, which
    `claude_runs` patches in. Every call finishes with `request.param`: {"exit": status, "out": stdout, "err": stderr}
    (by default 0, "added 1 package", "")."""
    reply = getattr(request, "param", {})
    run = create_autospec(subprocess.run)
    run.return_value = subprocess.CompletedProcess(
        ["npm"],
        reply.get("exit", 0),
        reply.get("out", "added 1 package"),
        reply.get("err", ""),
    )
    return run


@pytest.fixture
def claude_runs(
    request,
    monkeypatch,
    npm_installs,
):
    """subprocess.Popen as the Claude Code gateway starts claude: an autospecced MagicMock, patched into
    clean_eval.external.claude_code only, with `npm_installs` as its subprocess.run. `request.param` is how the run
    goes: {"exit": status, "out": its transcript, "timeouts": how many of the gateway's waits for it time out first,
    "interrupted": an exception the wait then raises instead, claude still running} (by default 0, "done", 0, None); or
    an exception, which starting claude raises. The process is `claude_runs.return_value`, and
    `claude_runs.identity_tokens` collects what the identity-token file held at each wait (nothing without one)."""
    outcome = getattr(request, "param", {})
    popen = create_autospec(subprocess.Popen)
    popen.identity_tokens = []
    if isinstance(outcome, BaseException):
        popen.side_effect = outcome
    else:
        process = popen.return_value
        process.communicate.side_effect = ClaudeProcess(
            popen,
            outcome,
        ).communicate
        process.returncode = process.poll.return_value = None  # running, until the wait ends
    monkeypatch.setattr(
        "clean_eval.external.claude_code.subprocess",
        SimpleNamespace(
            **{**vars(subprocess), "run": npm_installs, "Popen": popen},
        ),
    )
    return popen


@pytest.fixture
def oidc_tokens(
    request,
    monkeypatch,
):
    """GitHub's OIDC token endpoint as the Claude Code gateway calls it (urllib.request.urlopen): an autospecced
    MagicMock patched into clean_eval.external.claude_code only. `request.param` lists its replies to successive calls,
    each a token it issues (a str), a raw body (bytes) or an exception it raises; past them, or unparametrized, the
    n-th call issues "jwt-<n>" (tests.external.fakes.TokenEndpoint). Each call holds the Request the gateway made."""
    endpoint = TokenEndpoint(
        getattr(request, "param", []),
    )
    urlopen = create_autospec(urllib.request.urlopen, side_effect=endpoint.respond)
    monkeypatch.setattr(
        "clean_eval.external.claude_code.urllib",
        SimpleNamespace(
            parse=urllib.parse,
            request=SimpleNamespace(
                **{**vars(urllib.request), "urlopen": urlopen},
            ),
        ),
    )
    return urlopen


@pytest.fixture
def claude_installed(
    request,
    monkeypatch,
):
    """Whether `claude` is on the PATH the gateway looks on: `request.param`, True by default. shutil.which is mocked in
    clean_eval.external.claude_code only; the mock is returned."""
    which = MagicMock(
        return_value="/usr/bin/claude" if getattr(request, "param", True) else None,
    )
    monkeypatch.setattr(
        "clean_eval.external.claude_code.shutil",
        SimpleNamespace(
            which=which,
        ),
    )
    return which


@pytest.fixture
def run_folders(
    request,
    tmp_path,
):
    """The folders of a CI run, made under tmp_path (resolved): the workspace `ws`, and the output folder with the
    context and the agent's folder in it. The output folder is `request.param`, relative to tmp_path: "out" by default,
    "ws/out" for one inside the workspace."""
    base = tmp_path.resolve()
    out = base / getattr(request, "param", "out")
    folders = RunFolders(
        workspace=str(base / "ws"),
        out=str(out),
        context=str(out / "context"),
        agent=str(out / "agent"),
    )
    for folder in (folders.workspace, folders.context, folders.agent):
        Path(
            folder,
        ).mkdir(parents=True)
    return folders


@pytest.fixture
def claude_code(
    environ,
    settings,
    run_folders,
    claude_runs,
    claude_installed,
    oidc_tokens,
):
    """The Claude Code gateway of a run in `run_folders` whose environment holds `environ`: its `settings`, and the
    variables it passes on to the agent. It writes Claude Code's settings with FileSystemGateway, with `claude_runs`
    (and its `npm_installs`), `claude_installed` and `oidc_tokens` in place of the CLI, the PATH and GitHub's token
    endpoint."""
    variables = {name: value for name, value in environ.items() if value is not None}
    return ClaudeCodeGateway(
        FileSystemGateway(),
        settings,
        variables,
        GUARD,
        run_folders.out,
    )
