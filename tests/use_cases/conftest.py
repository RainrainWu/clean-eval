"""Fixtures for the use cases' tests: the external dependencies, one MagicMock per interface of
clean_eval/use_cases/interfaces.py, which a test passes to the use case under test, and the domain models that only the
use cases take.

- The mocks are specced to their interface, so a use case can only use what the interface declares.
- `git` and `fs` are stateful: they answer through an in-memory repository and file system (tests/use_cases/fakes.py,
  contract-tested against the real gateways in tests/external/) and record every call. Their contents come from the
  data fixtures `history`, `files` and `links`.
- The others (`clock`, `env`, `host`, `knowledge_graph`, `diagram_presenter`, `report_presenter`) are autospecced: a
  call that doesn't match the interface's signature fails. `request.param` configures them, as `configure_mock`'s
  keywords (`{"review_threads.return_value": (view, inline), "require_auth.side_effect": HeatmapError("...")}`).
- The data fixtures `graph`, `rating_cache` and `period` add the models the use cases take to the cross-layer ones of
  tests/conftest.py. `collected` is the merged-PR history an eval collects, built once per class.

A test can still change one call of any mock (`fs.read_text.side_effect = HeatmapError("...")`) and assert on the calls
(`host.dismiss_review.assert_called_once_with(...)`)."""

import os
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from clean_eval.domain.evaluation import Period
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Graph, RatingCache
from clean_eval.domain.review import AgentRun
from clean_eval.use_cases.evaluation import collect_history
from clean_eval.use_cases.interfaces import (
    Agent,
    Clock,
    CodeHost,
    DiagramPresenter,
    Environment,
    FileSystem,
    Git,
    KnowledgeGraph,
    ReportPresenter,
    RunLog,
)
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit

EMPTY_GRAPH: dict[str, Any] = {"nodes": {}, "links": {}, "tested": {}, "flows": [], "stats": {}}
# The period an eval looks back over: six weeks, with fixes looked for in the 30 days after it.
PERIOD: dict[str, Any] = {"since": "2026-01-15", "until": "2026-02-28"}
# What `collected` collects by default: a lone root commit (so no merged PR) over PERIOD, under the default policy.
COLLECTED: dict[str, Any] = {"history": [{"subject": "init"}], "period": PERIOD, "policy": None}
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
    store = MemoryFileSystem(files)
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
    mock = MagicMock(spec=FileSystem, wraps=memory_fs)
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
    mock = MagicMock(spec=Git, wraps=memory_git)
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
def environ(
    request,
):
    """The variables `env` holds: {name: value}, from `request.param`; none when not parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def env(
    environ,
):
    """An Environment answering from `environ`; its home is /home/user."""
    mock = create_autospec(Environment, instance=True)
    mock.variables.side_effect = lambda: dict(environ)
    mock.get.side_effect = lambda name, default=None: environ.get(name, default)
    mock.paths.side_effect = lambda name: [path for path in environ.get(name, "").split(os.pathsep) if path.strip()]
    mock.home.return_value = "/home/user"
    return mock


@pytest.fixture
def host(
    request,
):
    """A CodeHost; `request.param` configures it (configure_mock's keywords)."""
    mock = create_autospec(CodeHost, instance=True)
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def knowledge_graph(
    request,
):
    """A KnowledgeGraph; `request.param` configures it (configure_mock's keywords)."""
    mock = create_autospec(KnowledgeGraph, instance=True)
    mock.configure_mock(**getattr(request, "param", {}))
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
    `flows`, and `stats` or `has_flows` when they matter); an empty graph, with flows available, when not
    parametrized."""
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
    """The Period an eval looks back over: PERIOD (2026-01-15 to 2026-02-28, with fixes looked for in the 30 days
    after it), with the fields in `request.param` replaced."""
    return Period.model_validate({**PERIOD, **getattr(request, "param", {})})


# The merged-PR history an eval collects: rating every PR's base is the slow part, so it is built once per class.


@pytest.fixture(scope="class")
def collected(
    request,
):
    """The EvalHistory that collect_history gathers, with no code host, from the run in `request.param` over COLLECTED:
    `history`, the repository's commits (as the `history` fixture takes them); `period`, the Period's fields; and
    `policy`, risk-model.json overrides. By default a lone root commit, so no PR.

    It is class-scoped: parametrize it with `scope="class"`. The function-scoped `git` and `clock` can't serve it, so it
    makes its own: a Git mock over a MemoryGit, and a Clock on COLLECTED_ON."""
    run = {**COLLECTED, **getattr(request, "param", {})}
    memory_git = MemoryGit()
    for commit in run["history"]:
        memory_git.commit(**commit)
    clock = create_autospec(Clock, instance=True)
    clock.today.return_value = COLLECTED_ON
    return collect_history(
        MagicMock(spec=Git, wraps=memory_git),
        clock,
        Policy.from_overrides(run["policy"]),
        Period.model_validate(run["period"]),
    )


@pytest.fixture(scope="class")
def collected_json(
    collected,
):
    """`collected` as eval-history.json holds it, which is what the eval agent reads."""
    return collected.model_dump(mode="json")


@pytest.fixture(scope="class")
def collected_prs(
    collected_json,
):
    """The PRs of `collected_json`, by PR number."""
    return {entry["pr"]: entry for entry in collected_json["prs"]}


# A CI run's agent and log.


@pytest.fixture
def agent(
    request,
):
    """An Agent, which finishes (status 0) with an empty transcript unless `request.param` configures it (configure_mock's
    keywords), e.g. `{"run.return_value": AgentRun(status=1, transcript="...")}` or `{"require.side_effect": ...}`."""
    mock = create_autospec(Agent, instance=True)
    mock.run.return_value = AgentRun(status=0, transcript="")
    mock.configure_mock(**getattr(request, "param", {}))
    return mock


@pytest.fixture
def run_log():
    """A RunLog, recording the steps, warnings and texts a CI run reports."""
    return create_autospec(RunLog, instance=True)
