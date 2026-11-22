"""Rating a repository (use_cases/rate_repository.py): the pure rating of a snapshot's files, and rating a working tree
or a revision through fakes of the Git, FileSystem, Clock and KnowledgeGraph interfaces."""
import unittest

from tests.domain.builders import sf
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, fake

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.graph import Graph
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import RatingCache
from clean_eval.domain.source import Snapshot
from clean_eval.use_cases.interfaces import Clock, KnowledgeGraph
from clean_eval.use_cases.rate_repository import rate, rate_revision, rate_working_tree

P = Policy()
TODAY = "2026-11-16"
SNAP = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"
CLOCK = fake(Clock, today=lambda: TODAY)


def graph(has_flows=True):
    nodes = {"src/api/a.py::f": ("Function", "src/api/a.py", False), "src/core/c.py::g": ("Function", "src/core/c.py", False)}
    return Graph(nodes=nodes, links={"src/api/a.py": {"src/core/c.py"}}, tested={"src/api/a.py::f": {"tests/unit/test_a.py"}},
                 flows=[(0.9, "src/api/a.py", {"src/api/a.py", "src/core/c.py"})] if has_flows else [],
                 stats={"source": "fake", "nodes": 2, "edges": 1, "flows": 1 if has_flows else 0}, has_flows=has_flows)


def files():
    return [sf("src/api/a.py", 300, "def f(x: int) -> int:\n    return x\n"), sf("src/api/b.py", 300),
            sf("src/core/c.py", 100, CORE), sf("tests/unit/test_a.py", 50, test=True)]


class RateTest(unittest.TestCase):
    """The rating itself: a pure function of a snapshot's files and their knowledge graph."""

    def test_rates_every_module(self):
        h = rate(SNAP, files(), graph(), P, TODAY, depth=2)
        self.assertEqual((h.repo, h.ref, h.sha, h.generated_at, h.policy_version), ("demo", "main", "abc1234", TODAY, 2))
        self.assertEqual([m.name for m in h.modules], ["src/api", "src/core"])  # largest first; tests are not modules
        api, core = h.modules
        self.assertEqual((api.loc, api.tests.functions, api.tests.tested, api.tests.scales), (600, 1, 1, {"unit": 1}))
        self.assertEqual(api.baseline.model_dump(), api.scores.model_dump(exclude={"overall"}))
        self.assertEqual((api.facts.funcs, api.facts.typed_share), ([(2, 1)], 1.0))  # read from the source text
        self.assertEqual(core.facts.effect_counts, {"db_write": 2})
        self.assertEqual(core.facts.effect_examples, {"db_write": "src/core/c.py:2"})
        self.assertEqual(core.facts.fan_in, 1)
        self.assertTrue(h.notes[0].startswith("Knowledge graph (fake): 2 nodes"))
        self.assertEqual(h.loc_total, 700)

    def test_without_flows_the_facts_say_so(self):
        self.assertFalse(rate(SNAP, files(), graph(has_flows=False), P, TODAY, depth=2).modules[0].facts.flows_available)

    def test_excluded_and_missing_source(self):
        p = Policy.from_overrides({"exclude": ["src/core/*"]})
        names = [m.name for m in rate(SNAP, files(), graph(), p, TODAY, depth=2).modules]
        self.assertTrue(names and not any("core" in n for n in names), names)  # excluded code is not rated
        for source in ([], [sf("tests/unit/test_a.py", 50, test=True)]):
            with self.assertRaises(NoPythonSource):
                rate(SNAP, source, graph(), P, TODAY)



class RateRepositoryTest(unittest.TestCase):
    """A working tree is read through Git and the FileSystem, a revision from git objects alone."""

    def setUp(self):
        self.git = MemoryGit()
        self.first = self.git.commit("init", {"src/core/c.py": CORE, "tests/test_c.py": "from src.core.c import g\n"})
        self.fs = MemoryFileSystem({"/repo/src/core/c.py": CORE, "/repo/tests/test_c.py": "from src.core.c import g\n"})

    def test_a_working_tree_with_the_builtin_graph_and_coverage(self):
        self.fs.write_text("/repo/coverage.xml",
                           '<coverage><class name="c" filename="src/core/c.py" line-rate="0.5"/></coverage>')
        h = rate_working_tree(self.git, self.fs, CLOCK, P)
        self.assertEqual((h.generated_at, [m.name for m in h.modules]), (TODAY, ["src"]))
        self.assertEqual((h.modules[0].facts.line_rate, h.modules[0].tests.tested), (0.5, 1))
        self.assertTrue(h.notes[0].startswith("Knowledge graph (built-in ast)"))

    def test_a_working_tree_with_a_knowledge_graph(self):
        built = []
        knowledge_graph = fake(KnowledgeGraph, graph_of=lambda root, data_dir: built.append(
            (root, data_dir, self.fs.is_dir(data_dir))) or graph())
        h = rate_working_tree(self.git, self.fs, CLOCK, P, knowledge_graph)
        self.assertEqual(built, [("/repo", "/repo/.git/clean-eval-graph", True)])  # under .git by default, made for it
        self.assertTrue(h.notes[0].startswith("Knowledge graph (fake)"))
        self.assertTrue(h.modules[0].facts.flows_available)

    def test_revisions_share_their_work_through_the_cache(self):
        second = self.git.commit("more", {"src/core/d.py": "def h():\n    pass\n"})
        cache = RatingCache()
        old = rate_revision(self.git, CLOCK, self.first, P, cache)
        self.assertEqual((old.ref, old.sha, old.generated_at, old.modules[0].files), (self.first, self.first[:7], TODAY, 1))
        self.assertEqual(len(cache.blobs), 2)
        rate_revision(self.git, CLOCK, second, P, cache)
        self.assertEqual((len(cache.blobs), len(cache.syntax)), (3, 3))  # the unchanged files are read and parsed once

    def test_no_python_source(self):
        git = MemoryGit()
        git.commit("docs", {"README.md": "# r\n"})
        with self.assertRaises(NoPythonSource):
            rate_working_tree(git, MemoryFileSystem({"/repo/README.md": "# r\n"}), CLOCK, P)
        with self.assertRaises(NoPythonSource):
            rate_revision(git, CLOCK, "HEAD", P, RatingCache())


if __name__ == "__main__":
    unittest.main()
