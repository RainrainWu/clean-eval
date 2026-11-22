"""Rating a repository (use_cases/rate_repository.py): the pure rating of a snapshot's files, and rating a working tree
or a revision through fakes of the Git, FileSystem, Clock and KnowledgeGraph interfaces."""

import pytest

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.graph import Graph
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import RatingCache
from clean_eval.domain.source import Snapshot
from clean_eval.use_cases.interfaces import Clock, KnowledgeGraph
from clean_eval.use_cases.rate_repository import rate, rate_revision, rate_working_tree
from tests.domain.builders import sf
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, fake

P = Policy()
TODAY = "2026-11-16"
SNAP = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"
CLOCK = fake(Clock, today=lambda: TODAY)


def graph(has_flows=True):
    nodes = {
        "src/api/a.py::f": ("Function", "src/api/a.py", False),
        "src/core/c.py::g": ("Function", "src/core/c.py", False),
    }
    return Graph(
        nodes=nodes,
        links={"src/api/a.py": {"src/core/c.py"}},
        tested={"src/api/a.py::f": {"tests/unit/test_a.py"}},
        flows=[(0.9, "src/api/a.py", {"src/api/a.py", "src/core/c.py"})] if has_flows else [],
        stats={"source": "fake", "nodes": 2, "edges": 1, "flows": 1 if has_flows else 0},
        has_flows=has_flows,
    )


def files():
    return [
        sf("src/api/a.py", 300, "def f(x: int) -> int:\n    return x\n"),
        sf("src/api/b.py", 300),
        sf("src/core/c.py", 100, CORE),
        sf("tests/unit/test_a.py", 50, test=True),
    ]


# The rating itself: a pure function of a snapshot's files and their knowledge graph.


def test_rates_every_module():
    h = rate(SNAP, files(), graph(), P, TODAY, depth=2)
    assert (h.repo, h.ref, h.sha, h.generated_at, h.policy_version) == ("demo", "main", "abc1234", TODAY, 2)
    assert [m.name for m in h.modules] == ["src/api", "src/core"]  # largest first; tests are not modules
    api, core = h.modules
    assert (api.loc, api.tests.functions, api.tests.tested, api.tests.scales) == (600, 1, 1, {"unit": 1})
    assert api.baseline.model_dump() == api.scores.model_dump(exclude={"overall"})
    assert (api.facts.funcs, api.facts.typed_share) == ([(2, 1)], 1.0)  # read from the source text
    assert core.facts.effect_counts == {"db_write": 2}
    assert core.facts.effect_examples == {"db_write": "src/core/c.py:2"}
    assert core.facts.fan_in == 1
    assert h.notes[0].startswith("Knowledge graph (fake): 2 nodes")
    assert h.loc_total == 700


def test_without_flows_the_facts_say_so():
    assert not rate(SNAP, files(), graph(has_flows=False), P, TODAY, depth=2).modules[0].facts.flows_available


def test_excluded_code_is_not_rated():
    names = [
        m.name
        for m in rate(
            SNAP, files(), graph(), Policy.from_overrides({"exclude": ["src/core/*"]}), TODAY, depth=2
        ).modules
    ]
    assert names
    assert not any("core" in n for n in names), names


@pytest.mark.parametrize("source", [[], [sf("tests/unit/test_a.py", 50, test=True)]], ids=["no files", "tests only"])
def test_no_python_source_to_rate(source):
    with pytest.raises(NoPythonSource):
        rate(SNAP, source, graph(), P, TODAY)


# A working tree is read through Git and the FileSystem, a revision from git objects alone.


@pytest.fixture
def git(git):
    """One commit: a module that writes to a database, and its test."""
    git.commit("init", {"src/core/c.py": CORE, "tests/test_c.py": "from src.core.c import g\n"})
    return git


@pytest.fixture
def fs(fs):
    """The working tree of that commit."""
    fs.write_text("/repo/src/core/c.py", CORE)
    fs.write_text("/repo/tests/test_c.py", "from src.core.c import g\n")
    return fs


def test_a_working_tree_with_the_builtin_graph_and_coverage(git, fs):
    fs.write_text(
        "/repo/coverage.xml", '<coverage><class name="c" filename="src/core/c.py" line-rate="0.5"/></coverage>'
    )
    h = rate_working_tree(git, fs, CLOCK, P)
    assert (h.generated_at, [m.name for m in h.modules]) == (TODAY, ["src"])
    assert (h.modules[0].facts.line_rate, h.modules[0].tests.tested) == (0.5, 1)
    assert h.notes[0].startswith("Knowledge graph (built-in ast)")


def test_a_working_tree_with_a_knowledge_graph(git, fs):
    built = []
    knowledge_graph = fake(
        KnowledgeGraph, graph_of=lambda root, data_dir: built.append((root, data_dir, fs.is_dir(data_dir))) or graph()
    )
    h = rate_working_tree(git, fs, CLOCK, P, knowledge_graph)
    assert built == [("/repo", "/repo/.git/clean-eval-graph", True)]  # under .git by default, made for it
    assert h.notes[0].startswith("Knowledge graph (fake)")
    assert h.modules[0].facts.flows_available


def test_revisions_share_their_work_through_the_cache(git):
    first = git.rev_parse("HEAD")
    second = git.commit("more", {"src/core/d.py": "def h():\n    pass\n"})
    cache = RatingCache()
    old = rate_revision(git, CLOCK, first, P, cache)
    assert (old.ref, old.sha, old.generated_at, old.modules[0].files) == (first, first[:7], TODAY, 1)
    assert len(cache.blobs) == 2
    rate_revision(git, CLOCK, second, P, cache)
    assert (len(cache.blobs), len(cache.syntax)) == (3, 3)  # the unchanged files are read and parsed once


def test_a_repository_without_python_source():
    git = MemoryGit()
    git.commit("docs", {"README.md": "# r\n"})
    with pytest.raises(NoPythonSource):
        rate_working_tree(git, MemoryFileSystem({"/repo/README.md": "# r\n"}), CLOCK, P)
    with pytest.raises(NoPythonSource):
        rate_revision(git, CLOCK, "HEAD", P, RatingCache())
