"""Fixtures for the use cases' tests: the external dependencies, one MagicMock per interface of
clean_eval/use_cases/interfaces.py, which a test passes to the use case under test; the domain models that only the
use cases take; and the static assets the tests feed the use cases or check against.

- The mocks are specced to their interface, so a use case can only use what the interface declares.
- `git` and `fs` are stateful: they answer through an in-memory repository and file system (tests/use_cases/fakes.py,
  contract-tested against the real gateways in tests/external/) and record every call. Their contents come from the
  data fixtures `history`, `files` and `links`.
- The others (`clock`, `env`, `host`, `coverage`, `agent`, `runner`, `run_log`, `diagram_presenter`,
  `report_presenter`) are autospecced: a call that doesn't match the interface's signature fails. `clock`, `env` and
  `coverage` answer from data (`today`, `environ`, their param); `request.param` configures the rest, as
  `configure_mock`'s keywords (`{"post_review.return_value": ..., "require_auth.side_effect": HeatmapError("...")}`).
- The data fixtures `graph`, `rating_cache` and `period` add the models the use cases take to the cross-layer ones of
  tests/conftest.py. `collected` is the merged-PR history a retro collects, built once per class.
- The static assets (a CI run's folders and files, the sources and histories a rating reads, the changes a review
  assesses, a run's files, ...) take no param; each sits under a section comment naming what it serves. A test names
  one in its decorators with pytest-lazy-fixtures' `lf`, as a whole param or inside one
  (`@pytest.mark.parametrize("history", [lf("core_history")], indirect=True)`, `{"GH_TOKEN": lf("gh_token")}`), and
  derives a variant with `lfc`.

A test can still change one call of any mock (`fs.read_text.side_effect = HeatmapError("...")`) and assert on the calls
(`host.dismiss_review.assert_called_once_with(...)`)."""

import json
import os
from typing import Any
from unittest.mock import (
    MagicMock,
    create_autospec,
)

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import (
    Graph,
    RatingCache,
    Snapshot,
)
from clean_eval.domain.retro import (
    Period,
    ReviewThreads,
)
from clean_eval.domain.review import (
    AgentRun,
    RunFolders,
    Secrets,
)
from clean_eval.use_cases.heatmap import MAX_FILE_BYTES
from clean_eval.use_cases.interfaces import (
    Agent,
    Clock,
    CodeHost,
    CoverageReport,
    DiagramPresenter,
    Environment,
    FileSystem,
    Git,
    ReportPresenter,
    RunLog,
    Runner,
)
from clean_eval.use_cases.retro import collect_history
from clean_eval.use_cases.rules.heatmap import MARKERS
from tests.use_cases.fakes import (
    MemoryFileSystem,
    MemoryGit,
    SquashSubjects,
)

EMPTY_GRAPH: frozendict[str, Any] = frozendict({"nodes": {}, "links": {}, "tested": {}, "stats": {}})
# The period a retro looks back over: six weeks, with fixes looked for in the 30 days after it.
PERIOD: frozendict[str, Any] = frozendict({"since": "2026-01-15", "until": "2026-02-28"})
COLLECTED_ON = "2026-03-01"  # the day `collected` rates the PRs' bases on

# The file system and the repository: in memory, recording every call.


@pytest.fixture
def files(
    request,
):
    """The files `fs` starts with: {path: text}, from `request.param`; none when not parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def links(
    request,
):
    """The symlinks `fs` starts with: {path: target}, from `request.param`; none when not parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def memory_fs(
    files,
    links,
):
    """The in-memory file system behind `fs`, holding `files` and `links` (POSIX paths; relative ones resolve against
    /work). Read it to check what the code under test left behind without recording a call on `fs`."""
    store = MemoryFileSystem(
        files,
    )
    for path, target in links.items():
        store.link(path, target)
    return store


@pytest.fixture
def fs(
    request,
    memory_fs,
):
    """A FileSystem: a MagicMock that answers through `memory_fs`. `request.param` configures it (configure_mock's
    keywords), e.g. to make one method fail."""
    mock = MagicMock(
        spec=FileSystem,
        wraps=memory_fs,
    )
    mock.sep = memory_fs.sep
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def history(
    request,
):
    """The commits `git` holds, oldest first: one dict of MemoryGit.commit's arguments per commit (`subject`, `files`
    {path: text}, `when` (a datetime or an ISO date), `author`, `remove`), from `request.param`; none when not
    parametrized."""
    return list(getattr(request, "param", []))


@pytest.fixture
def memory_git(
    history,
):
    """The in-memory repository behind `git`: "demo" at /repo, on branch main, with the commits of `history`."""
    store = MemoryGit()
    for commit in history:
        store.commit(**commit)
    return store


@pytest.fixture
def shas(
    memory_git,
):
    """The shas of the commits of `history`, oldest first."""
    return [c["sha"] for c in memory_git.commits]


@pytest.fixture
def git(
    request,
    memory_git,
):
    """A Git: a MagicMock that answers through `memory_git`. `request.param` configures it (configure_mock's
    keywords), e.g. to make one method answer otherwise."""
    mock = MagicMock(
        spec=Git,
        wraps=memory_git,
    )
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


# The other dependencies: autospecced, configured by `request.param`.


@pytest.fixture
def clock(
    today,
):
    """A Clock whose today() is the `today` fixture."""
    mock = create_autospec(Clock, instance=True)
    mock.today.return_value = today
    return mock


@pytest.fixture
def env(
    environ,
):
    """An Environment answering from `environ` (tests/conftest.py), without its None (unset) variables, every value of
    which is a secret (a test's environment holds only what it means); its home is /home/user."""
    variables = {name: value for name, value in environ.items() if value is not None}
    mock = create_autospec(Environment, instance=True)
    mock.variables.side_effect = lambda: dict(variables)
    mock.secrets.side_effect = lambda: Secrets(
        values=tuple(variables.values()),
    )
    mock.paths.side_effect = lambda name: [path for path in variables.get(name, "").split(os.pathsep) if path.strip()]
    mock.home.return_value = "/home/user"
    return mock


@pytest.fixture
def host(
    request,
):
    """A CodeHost, which numbers a merged PR from the last "(#N)" of its subject (SquashSubjects); `request.param`
    configures it (configure_mock's keywords)."""
    mock = create_autospec(CodeHost, instance=True)
    mock.merged_pr.side_effect = SquashSubjects().merged_pr
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def coverage(
    request,
):
    """A CoverageReport whose line rates ({path: share of lines covered}) are `request.param`; none when not
    parametrized."""
    mock = create_autospec(CoverageReport, instance=True)
    mock.line_rates.return_value = dict(getattr(request, "param", {}))
    return mock


@pytest.fixture
def diagram_presenter(
    request,
):
    """A DiagramPresenter; `request.param` configures it (configure_mock's keywords)."""
    mock = create_autospec(DiagramPresenter, instance=True)
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def report_presenter(
    request,
):
    """A ReportPresenter; `request.param` configures it (configure_mock's keywords)."""
    mock = create_autospec(ReportPresenter, instance=True)
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


# The domain models that only the use cases take.


@pytest.fixture
def graph(
    request,
):
    """A knowledge Graph, validated from `request.param` (Graph fields over EMPTY_GRAPH: `nodes`, `links`, `tested`,
    and `stats` when it matters); an empty graph when not parametrized."""
    return Graph.model_validate({**EMPTY_GRAPH, **getattr(request, "param", {})})


@pytest.fixture
def rating_cache(
    request,
):
    """What rating many revisions shares (a RatingCache): empty, or validated from `request.param` (`blobs`, `syntax`,
    `signals`)."""
    return RatingCache.model_validate(getattr(request, "param", {}))


@pytest.fixture
def period(
    request,
):
    """The Period a retro looks back over: PERIOD (2026-01-15 to 2026-02-28, with fixes looked for in the 30 days
    after it), with the fields in `request.param` replaced."""
    return Period.model_validate({**PERIOD, **getattr(request, "param", {})})


# The merged-PR history a retro collects: rating every PR's base is the slow part, so it is built once per class.


@pytest.fixture(scope="class")
def fix_and_revert_history():
    """The commits of a period whose PRs let defects escape, as `history` takes them: #1 changes the billing code, #2
    only docs and #4 the users code; then #3 fixes the billing code #1 changed, and #5 reverts #4. Class-scoped, as
    `collected` is, whose default it is."""
    return [
        {
            "subject": "init",
            "files": {"src/billing/a.py": "a\n" * 20, "src/users/u.py": "u\n" * 20},
            "when": "2026-01-01T12:00",
        },
        {
            "subject": "feat: billing discounts (#1)",
            "files": {"src/billing/a.py": "b\n" * 25},
            "when": "2026-02-01T12:00",
        },
        {"subject": "docs: notes (#2)", "files": {"docs/n.md": "n\n"}, "when": "2026-02-02T12:00"},
        {"subject": "feat: users export (#4)", "files": {"src/users/u.py": "v\n" * 22}, "when": "2026-02-03T12:00"},
        {
            "subject": "fix: billing rounding (#3)",
            "files": {"src/billing/a.py": "c\n" * 25},
            "when": "2026-02-05T12:00",
        },
        {
            "subject": 'Revert "feat: users export (#4)" (#5)',
            "files": {"src/users/u.py": "u\n" * 20},
            "when": "2026-02-06T12:00",
        },
    ]


@pytest.fixture(scope="class")
def collected(
    request,
    fix_and_revert_history,
):
    """The RetroHistory that collect_history gathers, without the reviews, from the run in `request.param`: `history`,
    the repository's commits (as the `history` fixture takes them), by default `fix_and_revert_history`; `period`, the
    Period's fields, by default PERIOD; and `policy`, risk-model.json overrides, by default none.

    It is class-scoped: parametrize it with `scope="class"`. A param that holds an `lf` is collected anew for each
    test, since pytest can't match it to the param it resolved. The function-scoped `git`, `host` and `clock` can't
    serve it, so it makes its own: a Git mock over a MemoryGit, a CodeHost numbering PRs as SquashSubjects does, and a
    Clock on COLLECTED_ON."""
    run = {"history": fix_and_revert_history, "period": PERIOD, "policy": None, **getattr(request, "param", {})}
    memory_git = MemoryGit()
    for commit in run["history"]:
        memory_git.commit(**commit)
    clock = create_autospec(Clock, instance=True)
    clock.today.return_value = COLLECTED_ON
    host = create_autospec(CodeHost, instance=True)
    host.merged_pr.side_effect = SquashSubjects().merged_pr
    return collect_history(
        MagicMock(
            spec=Git,
            wraps=memory_git,
        ),
        host,
        clock,
        Policy.from_overrides(run["policy"]),
        Period.model_validate(run["period"]),
    )


@pytest.fixture(scope="class")
def collected_json(
    collected,
):
    """`collected` as retro-history.json holds it, which is what the retro agent reads."""
    return collected.model_dump(mode="json")


@pytest.fixture(scope="class")
def collected_prs(
    collected_json,
):
    """The PRs of `collected_json`, by PR number."""
    return {entry["pr"]: entry for entry in collected_json["prs"]}


# A CI run's agent, runner and log.


@pytest.fixture
def agent(
    request,
):
    """An Agent, which finishes (status 0) with an empty transcript unless `request.param` configures it
    (configure_mock's keywords), e.g. `{"run.return_value": AgentRun(status=1, transcript="...")}` or
    `{"require.side_effect": ...}`."""
    mock = create_autospec(Agent, instance=True)
    mock.run.return_value = AgentRun(
        status=0,
        transcript="",
    )
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def runner():
    """A Runner, recording the step outputs and the job summary a CI run reports."""
    return create_autospec(Runner, instance=True)


@pytest.fixture
def run_log():
    """A RunLog, recording the steps, warnings and texts a CI run reports."""
    return create_autospec(RunLog, instance=True)


# A CI run (use_cases/ci.py, and the actions' runs of the heat map, the review and the retro): its folders, what the
# runner holds before it, its environment, and the secret nothing the agent leaves may keep.


@pytest.fixture
def run_folders():
    """The folders of a CI run on /repo (RunFolders): the workspace /repo, and the output folder /out with the context
    and the agent's folder in it."""
    return RunFolders(
        workspace="/repo",
        out="/out",
        context="/out/context",
        agent="/out/agent",
    )


@pytest.fixture
def files_before_a_run():
    """What a runner holds before a run, as `files` takes it: an earlier run's output folder (its context, the agent's
    result, the report, the transcript, and a file that isn't the run's)."""
    return {
        "/out/context/old.json": "{}",
        "/out/agent/result.json": "{}",
        "/out/report.md": "old",
        "/out/claude.log": "old",
        "/out/keep.txt": "not the run's",
    }


@pytest.fixture
def run_environment():
    """The variables of a CI run on /repo with its outputs in /out (the folders of `run_folders`)."""
    return {"CE_WORKSPACE": "/repo", "CE_OUTPUT_DIR": "/out"}


@pytest.fixture
def gh_token():
    """The GitHub token a runner holds as GH_TOKEN (not a real one): a secret that no transcript, and nothing else the
    agent leaves, may keep."""
    return "ghs_" + "t" * 36


@pytest.fixture
def empty_agent_folder():
    """The files of a run whose agent wrote nothing, as `files` takes them: a .keep file, which makes the output folder
    and the agent's folder in it."""
    return {"/out/agent/.keep": ""}


# The sources a rating reads (use_cases/heatmap.py): a history of two commits, the second adding a generated file, a
# huge one and one the working tree has lost; and the working tree, which differs from it.


@pytest.fixture
def first_commit_files():
    """The files of the first commit of `sources_history`, "init" ({path: text}): a module and its test."""
    return {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"}


@pytest.fixture
def second_commit_files():
    """The files the second commit of `sources_history`, "change", writes ({path: text}): src/a.py changed, a generated
    file, src/gone.py (which the working tree has lost), a file too big to read, and a document."""
    return {
        "src/a.py": "def f():\n    return 2\n",
        "src/x_pb2.py": "X = 1\n",
        "src/gone.py": "G = 1\n",
        "src/huge.py": "#\n" * (MAX_FILE_BYTES // 2 + 1),
        "docs/n.md": "n\n",
    }


@pytest.fixture
def sources_history(
    first_commit_files,
    second_commit_files,
):
    """A history of two commits, as `history` takes it: "init" (`first_commit_files`), then "change"
    (`second_commit_files`)."""
    return [{"subject": "init", "files": first_commit_files}, {"subject": "change", "files": second_commit_files}]


@pytest.fixture
def tracked_paths(
    first_commit_files,
    second_commit_files,
):
    """What git tracks in the working tree of `sources_history`, sorted: its last commit's files, and a symlink that is
    staged (`staged_symlink`)."""
    return sorted({*first_commit_files, *second_commit_files, "src/link.py"})


@pytest.fixture
def edited_source():
    """src/a.py as the working tree of `sources_history` holds it: edited since the last commit."""
    return "def f():\n    return 3\n"


@pytest.fixture
def working_tree_files(
    first_commit_files,
    second_commit_files,
    edited_source,
):
    """The working tree of `sources_history` at /repo, as `files` takes it: the last commit without src/gone.py, with
    src/a.py edited (`edited_source`) and an untracked file."""
    tree = {**first_commit_files, **second_commit_files, "src/a.py": edited_source, "src/untracked.py": "U = 1\n"}
    return {"/repo/" + path: text for path, text in tree.items() if path != "src/gone.py"}


@pytest.fixture
def staged_symlink():
    """The symlink in the working tree of `sources_history`, as `links` takes it: src/link.py, which git has staged, to
    a file outside the repository."""
    return {"/repo/src/link.py": "/etc/hosts"}


@pytest.fixture
def demo_snapshot():
    """The snapshot rated: repository "demo" at main (abc1234), read from /r."""
    return Snapshot(
        repo="demo",
        ref="main",
        sha="abc1234",
        root="/r",
    )


@pytest.fixture
def core_source():
    """The source of src/core/c.py: a function that writes to a database (an INSERT, then a commit)."""
    return "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"


@pytest.fixture
def unit_test_file():
    """A unit test, as `source_files` takes a file: tests/unit/test_a.py, of 50 lines."""
    return {"path": "tests/unit/test_a.py", "loc": 50, "is_test": True}


@pytest.fixture
def api_and_core_sources(
    core_source,
    unit_test_file,
):
    """The files of `demo_snapshot`, as `source_files` takes them: src/api/a.py (a typed function) and b.py, of 300
    lines each; src/core/c.py (`core_source`), of 100; and `unit_test_file`."""
    return [
        {"path": "src/api/a.py", "loc": 300, "text": "def f(x: int) -> int:\n    return x\n"},
        {"path": "src/api/b.py", "loc": 300},
        {"path": "src/core/c.py", "loc": 100, "text": core_source},
        unit_test_file,
    ]


@pytest.fixture
def api_and_core_graph():
    """The knowledge graph of `api_and_core_sources`, as `graph` takes it: src/api/a.py links to src/core/c.py, and its
    function f is tested by tests/unit/test_a.py."""
    return {
        "nodes": {
            "src/api/a.py::f": ("Function", "src/api/a.py", False),
            "src/core/c.py::g": ("Function", "src/core/c.py", False),
        },
        "links": {"src/api/a.py": {"src/core/c.py"}},
        "tested": {"src/api/a.py::f": {"tests/unit/test_a.py"}},
        "stats": {"source": "fake", "nodes": 2, "edges": 1},
    }


# Rating a repository: one commit of a module that writes to a database and its test, its working tree, and a second
# commit; and a repository without Python source.


@pytest.fixture
def core_test_source():
    """The source of tests/test_c.py, which tests src/core/c.py (`core_source`)."""
    return "from src.core.c import g\n"


@pytest.fixture
def core_history(
    core_source,
    core_test_source,
):
    """A history of one commit, "init", as `history` takes it: src/core/c.py (`core_source`) and its test
    (`core_test_source`)."""
    return [{"subject": "init", "files": {"src/core/c.py": core_source, "tests/test_c.py": core_test_source}}]


@pytest.fixture
def core_checkout(
    core_source,
    core_test_source,
):
    """The working tree of `core_history` at /repo, as `files` takes it."""
    return {"/repo/src/core/c.py": core_source, "/repo/tests/test_c.py": core_test_source}


@pytest.fixture
def core_line_rates():
    """The line rates the coverage report of `core_checkout` holds: src/core/c.py has half its lines covered."""
    return {"src/core/c.py": 0.5}


@pytest.fixture
def added_module_source():
    """The source of src/core/d.py, the module file the second commit of `two_commit_core_history` adds."""
    return "def h():\n    pass\n"


@pytest.fixture
def two_commit_core_history(
    core_history,
    added_module_source,
):
    """`core_history`, then a second commit, "more", which adds one module file, src/core/d.py
    (`added_module_source`)."""
    return [*core_history, {"subject": "more", "files": {"src/core/d.py": added_module_source}}]


@pytest.fixture
def docs_only_history():
    """A history without Python source, as `history` takes it: one commit, "docs", of a README."""
    return [{"subject": "docs", "files": {"README.md": "# r\n"}}]


@pytest.fixture
def docs_only_checkout():
    """The working tree of `docs_only_history` at /repo, as `files` takes it."""
    return {"/repo/README.md": "# r\n"}


# Summarizing a heat map and refreshing the README: a reviewer's levels, the README's and the diagram's paths, and what
# the DiagramPresenter answers; and in a CI run, the levels the agent writes.


@pytest.fixture
def critical_business_levels():
    """A reviewer's levels, as `judgement` takes them: module "m"'s Business Impact at level 0, the Critical band."""
    return {"modules": {"m": {"business": {"level": 0, "reason": "r"}}}}


@pytest.fixture
def readme_path():
    """The README whose heat-map section is refreshed."""
    return "/repo/README.md"


@pytest.fixture
def svg_path():
    """The heat map's diagram, beside the README in its docs/ folder."""
    return "/repo/docs/risk-heatmap.svg"


@pytest.fixture
def diagram_replies():
    """What the DiagramPresenter answers, as `diagram_presenter` takes it (configure_mock's keywords): an SVG, and a
    README block between the markers that holds the image link and the fingerprint it is given (str.format fills them
    in from the call's arguments)."""
    block = MARKERS[0] + "\n![map]({1})\n<!-- risk-heat-map:fingerprint={2} -->\n" + MARKERS[1] + "\n"
    return {"svg.return_value": "<svg/>", "readme_block.side_effect": block.format}


@pytest.fixture
def agent_levels(
    gh_token,
):
    """judgement.json as the agent may write it: module "m"'s Business Impact at level 0, with a reason that quotes
    `gh_token`, and an entry that is no aspect, at level 9."""
    return '{"modules": {"m": {"business": {"level": 0, "reason": "read %s"}, "nope": {"level": 9}}}}' % gh_token


# The PR review (use_cases/review.py): the changes it assesses and splits into patches, as `history` takes them, git's
# diffs of a rename, and where a base keeps its policy.


@pytest.fixture
def api_and_core_tree(
    core_source,
):
    """The files of the base of `core_change` ({path: text}): src/api/a.py and b.py, of 150 lines each, src/core/c.py
    (`core_source`) and a test."""
    return {
        "src/api/a.py": "a = 1\n" * 150,
        "src/api/b.py": "b = 1\n" * 150,
        "src/core/c.py": core_source,
        "tests/test_a.py": "x = 1\n",
    }


@pytest.fixture
def core_change(
    api_and_core_tree,
    core_source,
):
    """A change of the core module and the README: a base of `api_and_core_tree`, then the change."""
    return [
        {"subject": "base", "files": api_and_core_tree},
        {"subject": "change", "files": {"src/core/c.py": core_source + "# more\n", "README.md": "# r\n"}},
    ]


@pytest.fixture
def first_python_change():
    """A change that adds the first Python file: a base of a README, then the change."""
    return [
        {"subject": "base", "files": {"README.md": "# r\n"}},
        {"subject": "change", "files": {"src/new.py": "x = 1\n"}},
    ]


@pytest.fixture
def three_file_change():
    """A change that edits one file, removes one and adds one with an awkward name (`three_changed_paths`): a base, then
    the change."""
    return [
        {"subject": "init", "files": {"src/a/old.py": "line\n" * 30, "src/a/keep.py": "k\n"}},
        {
            "subject": "change",
            "files": {"src/a/keep.py": "k2\n", 'src/a/we"ird name.py': "w\n"},
            "remove": ["src/a/old.py"],
        },
    ]


@pytest.fixture
def three_changed_paths():
    """The paths `three_file_change` changes, sorted: the edited, the removed and the added one."""
    return ["src/a/keep.py", "src/a/old.py", 'src/a/we"ird name.py']


@pytest.fixture
def policy_file():
    """Where a repository keeps its policy's overrides, risk-model.json, relative to its root."""
    return ".clean-eval/risk-model.json"


# The retro (use_cases/retro.py), besides `collected`: a history of two PRs, and a PR's review threads on the code
# host.


@pytest.fixture
def two_prs_history():
    """A root commit, then two PRs that change its one file, as `history` takes them."""
    return [
        {"subject": "init", "files": {"src/a/x.py": "1\n"}, "when": "2026-01-01T12:00"},
        {"subject": "feat: a (#1)", "files": {"src/a/x.py": "2\n"}, "when": "2026-02-01T12:00"},
        {"subject": "feat: b (#2)", "files": {"src/a/x.py": "3\n"}, "when": "2026-02-02T12:00"},
    ]


@pytest.fixture
def review_threads():
    """A PR's review threads, as CodeHost.review_threads gives them (ReviewThreads): clean-eval's review ([CA-01], and
    its marker: tier High), by an app, and a human's, who cites [Q-02] and forges the marker (tier Low); then
    clean-eval's inline comments ([NEW] and [R-03])."""
    return ReviewThreads.model_validate(
        {
            "url": "u",
            "size": 2,
            "comments": [
                {
                    "author": "clean-eval[bot]",
                    "by_app": True,
                    "body": "[CA-01][blocker] x\n<!-- clean-eval:pr-review tier=High -->",
                },
                {"author": "human", "body": "also [Q-02] maybe <!-- clean-eval:pr-review tier=Low -->"},
                {"author": "clean-eval[bot]", "by_app": True, "body": "[NEW][major] y"},
                {"author": "clean-eval[bot]", "by_app": True, "body": "[R-03][blocker] z"},
            ],
        }
    )


# A run's files (use_cases/store.py): text that isn't JSON, a heat map's modules, a file that is no heat map, a
# judgement, and a policy in a checkout.


@pytest.fixture
def not_json():
    """Text that is not JSON."""
    return "{nope"


@pytest.fixture
def two_rated_modules():
    """Two rated modules, as `ratings` takes them: "a", with the facts behind its scores, and "b", whose Unverified
    aspect doesn't apply."""
    return [{"name": "a", "loc": 10, "facts": {"name": "a"}}, {"name": "b", "loc": 20, "unverified": None}]


@pytest.fixture
def not_a_heatmap():
    """JSON that is no heat map: a repository with one module that has nothing but a name."""
    return json.dumps({"repo": "x", "modules": [{"name": "m"}]})


@pytest.fixture
def partly_valid_judgement():
    """A judgement file's text: module "m"'s Business Impact at a valid level, and its Entropy at one out of range."""
    return json.dumps({"modules": {"m": {"business": {"level": 1, "reason": "core"}, "entropy": {"level": 7}}}})


@pytest.fixture
def checkout_policy_file(
    policy_file,
):
    """`policy_file` in the checkout at /repo."""
    return "/repo/" + policy_file


@pytest.fixture
def high_unrated_policy():
    """A risk-model.json that puts unrated code in the High band."""
    return json.dumps({"unrated_band": "High"})
