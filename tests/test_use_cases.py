"""The use cases: module-level functions given implementations of the interfaces in use_cases/interfaces.py. Here they
get in-memory fakes of those interfaces (helpers.fake), or the real gateways over throwaway repositories and folders."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import Repo, days_ago, facts, fake, heatmap, rating, read, sf, write

from clean_eval.domain.errors import HeatmapError, InvalidPolicy, NoPythonSource
from clean_eval.domain.graph import Graph
from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import RatingCache
from clean_eval.domain.source import Snapshot
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.use_cases.assess_change import assess_change
from clean_eval.use_cases.coverage import coverage_line_rates
from clean_eval.use_cases.finalize_heatmap import finalize_heatmap
from clean_eval.use_cases.interfaces import Clock, DiagramPresenter, Git, KnowledgeGraph, ReportPresenter
from clean_eval.use_cases.rate_repository import rate, rate_revision, rate_working_tree
from clean_eval.use_cases.rules.readme import MARKERS
from clean_eval.use_cases.sources import GENERATED, content_id, count_loc, read_revision, read_working_tree
from clean_eval.use_cases.store import load_heatmap, load_judgement, load_policy, read_json, save_heatmap
from clean_eval.use_cases.summarize_heatmap import summarize_heatmap
from clean_eval.use_cases.update_readme import update_readme

P = Policy()
FS = FileSystemGateway()
TODAY = "2026-11-16"
CLOCK = fake(Clock, today=lambda: TODAY)
SNAP = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"


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
    """Rating a working tree or a revision through the Git and FileSystem interfaces (the real gateways here)."""

    def setUp(self):
        self.repo = Repo()
        self.first = self.repo.commit("init", {"src/core/c.py": CORE, "tests/test_c.py": "from src.core.c import g\n"},
                                      when=days_ago(2))
        self.git = GitGateway(self.repo.path)

    def tearDown(self):
        self.repo.cleanup()

    def test_working_tree_with_the_builtin_graph_and_coverage(self):
        write(os.path.join(self.repo.path, "coverage.xml"),
              '<coverage><class name="c" filename="src/core/c.py" line-rate="0.5"/></coverage>')
        h = rate_working_tree(self.git, FS, CLOCK, P)
        self.assertEqual((h.generated_at, [m.name for m in h.modules]), (TODAY, ["src"]))
        self.assertEqual((h.modules[0].facts.line_rate, h.modules[0].tests.tested), (0.5, 1))
        self.assertTrue(h.notes[0].startswith("Knowledge graph (built-in ast)"))

    def test_working_tree_with_a_knowledge_graph(self):
        built = []
        knowledge_graph = fake(KnowledgeGraph, graph_of=lambda root, data_dir: built.append((root, FS.is_dir(data_dir)))
                               or graph())
        h = rate_working_tree(self.git, FS, CLOCK, P, knowledge_graph, FS.join(self.repo.path, "graph-data"))
        self.assertEqual(built, [(self.git.toplevel(), True)])  # built into a folder made for it
        self.assertTrue(h.notes[0].startswith("Knowledge graph (fake)"))
        self.assertTrue(h.modules[0].facts.flows_available)

    def test_revisions_share_their_work_through_the_cache(self):
        second = self.repo.commit("more", {"src/core/d.py": "def h():\n    pass\n"}, when=days_ago(1))
        cache = RatingCache()
        old = rate_revision(self.git, CLOCK, self.first, P, cache)
        self.assertEqual((old.ref, old.generated_at, old.modules[0].files), (self.first, TODAY, 1))
        self.assertEqual(len(cache.blobs), 2)
        rate_revision(self.git, CLOCK, second, P, cache)
        self.assertEqual((len(cache.blobs), len(cache.syntax)), (3, 3))  # the unchanged files are read and parsed once

    def test_no_python_source(self):
        empty = Repo()
        try:
            empty.commit("docs", {"README.md": "# r\n"})
            with self.assertRaises(NoPythonSource):
                rate_working_tree(GitGateway(empty.path), FS, CLOCK, P)
            with self.assertRaises(NoPythonSource):
                rate_revision(GitGateway(empty.path), CLOCK, "HEAD", P, RatingCache())
        finally:
            empty.cleanup()


def fake_git(tree, changed=()):
    """A Git whose revisions all hold `tree` ({path: text}) and differ by the `changed` paths."""
    return fake(Git, ls_tree=lambda rev: [(p, "blob:" + p, len(t)) for p, t in tree.items()],
                read_blobs=lambda ids: {i: tree[i[len("blob:"):]] for i in ids}, rev_parse=lambda rev, short=False: "abc1234",
                repo_name=lambda: "demo", toplevel=lambda: "/r", diff_files=lambda base, head: list(changed))


class AssessChangeTest(unittest.TestCase):
    TREE = {"src/api/a.py": "a = 1\n" * 150, "src/api/b.py": "b = 1\n" * 150, "src/core/c.py": CORE, "tests/test_a.py": "x = 1\n"}

    def test_change_risk_from_the_base_heat_map(self):
        heat, risk = assess_change(fake_git(self.TREE, ["src/core/c.py", "README.md"]), CLOCK, "base", "head", P)
        self.assertEqual([m.module for m in risk.modules], ["src/core"])
        self.assertEqual(risk.max_band, risk.modules[0].band)
        self.assertEqual(risk.ignored, ["README.md"])

    def test_a_base_without_python_rates_everything_unrated(self):
        heat, risk = assess_change(fake_git({}, ["src/new.py"]), CLOCK, "base", "head", P)
        self.assertEqual((heat.repo, heat.sha, heat.generated_at), ("demo", "abc1234", TODAY))  # the base's snapshot
        self.assertEqual((heat.modules, risk.max_band, risk.modules[0].rated), ([], "Moderate", False))


DIAGRAM = fake(DiagramPresenter, svg=lambda heatmap: "<svg/>", readme_block=lambda heatmap, link, fp: "%s\n![map](%s)\n"
               "<!-- risk-heat-map:fingerprint=%s -->\n%s\n" % (MARKERS[0], link, fp, MARKERS[1]))


class SummarizeHeatmapTest(unittest.TestCase):
    def test_levels_are_applied_before_summarizing(self):
        report = fake(ReportPresenter, report=lambda heatmap: "%d judged" % heatmap.judged_modules)
        judged = Judgement.model_validate({"modules": {"m": {"business": {"level": 0, "reason": "r"}}}})
        final, text = summarize_heatmap(report, heatmap(rating()), judged, P)
        self.assertEqual((text, final.judged_modules), ("1 judged", 1))
        self.assertGreaterEqual(final.modules[0].scores.biz, 75)
        self.assertEqual(finalize_heatmap(heatmap(rating()), None, P).judged_modules, 0)


class UpdateReadmeTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.readme, self.svg = os.path.join(self.dir, "README.md"), os.path.join(self.dir, "docs", "risk-heatmap.svg")
        write(self.readme, "# Demo\n")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def update(self, h, readme=None):
        return update_readme(FS, DIAGRAM, readme or self.readme, self.svg, h, None, P)

    def test_writes_when_ratings_change_and_not_otherwise(self):
        first = self.update(heatmap(rating()))
        self.assertEqual((first.changed, first.action), (True, "ADD"))
        self.assertIn("![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint, read(self.readme))
        self.assertEqual(read(self.svg), "<svg/>")
        self.assertFalse(self.update(heatmap(rating())).changed)  # the same ratings: nothing to write
        os.remove(self.svg)                                       # diagram deleted: rewritten though the ratings match
        self.assertTrue(self.update(heatmap(rating())).changed)
        moved = self.update(heatmap(rating(biz=90)))
        self.assertEqual((moved.changed, moved.action), (True, "UPDATE"))

    def test_the_image_is_linked_relative_to_the_readme(self):
        nested = os.path.join(self.dir, "docs", "README.md")
        self.update(heatmap(rating()), nested)
        self.assertIn("![map](risk-heatmap.svg)", read(nested))
        self.assertTrue(read(nested).endswith("\n"))


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.repo = r = Repo()
        self.old = r.commit("init", {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"}, when=days_ago(3))
        r.commit("change", {"src/a.py": "def f():\n    return 2\n\n\ndef g():\n    pass\n", "src/x_pb2.py": "X = 1\n"}, when=days_ago(2))
        write(os.path.join(r.path, "src", "untracked.py"), "U = 1\n")
        os.symlink("/etc/hosts", os.path.join(r.path, "src", "link.py"))
        r.git("add", "src/link.py")
        self.git = GitGateway(r.path)

    def tearDown(self):
        self.repo.cleanup()

    def test_working_tree_reads_tracked_python_only(self):
        snap, read_files = read_working_tree(self.git, FS, P)
        files = {f.path: f for f in read_files}
        self.assertEqual(sorted(files), ["src/a.py", "tests/test_a.py"])  # no untracked, generated or symlinked file
        self.assertTrue(files["tests/test_a.py"].is_test)
        self.assertEqual(files["src/a.py"].blob, self.repo.git("rev-parse", "HEAD:src/a.py"))  # same id as git
        self.assertEqual((snap.ref, snap.sha), ("main", self.repo.git("rev-parse", "--short", "HEAD")))

    def test_revision_reads_git_objects_and_shares_contents(self):
        blobs = {}
        snap, old = read_revision(self.git, self.old, P, blobs)
        self.assertEqual({f.path: f.text for f in old}["src/a.py"], "def f():\n    return 1\n")
        self.assertEqual((snap.ref, len(blobs)), (self.old, 2))
        read_revision(self.git, "HEAD", P, blobs)
        self.assertEqual(len(blobs), 3)  # the unchanged test file is read once

    def test_helpers(self):
        self.assertEqual(count_loc("a = 1\n\n# note\n  \nb = 2\n"), 2)
        self.assertTrue(all(GENERATED.search("/" + p) for p in ("x_pb2.py", "a/generated/b.py", "vendor/x.py")))
        self.assertEqual(content_id("hi\n"), subprocess.run(["git", "hash-object", "--stdin"], input="hi\n", capture_output=True,
                                                            text=True).stdout.strip())


class RunFilesTest(unittest.TestCase):
    """The files of a run, read and written through the FileSystem interface."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_coverage_report_line_rates(self):
        self.assertEqual(coverage_line_rates(FS, self.dir), {})
        write(os.path.join(self.dir, "coverage.xml"), '<coverage><class name="a" filename="src/a.py" line-rate="0.75"/></coverage>')
        self.assertEqual(coverage_line_rates(FS, self.dir), {"src/a.py": 0.75})

    def test_the_heatmap_round_trips_with_its_facts(self):
        path = os.path.join(self.dir, "out", "data.json")
        h = heatmap(rating("a", 10, fact=facts(name="a")), rating("b", 20, unverified=None))
        save_heatmap(FS, h, path)
        self.assertEqual(load_heatmap(FS, path), h)
        with self.assertRaises(HeatmapError):
            load_heatmap(FS, os.path.join(self.dir, "missing.json"))
        write(path, json.dumps({"repo": "x", "modules": [{"name": "m"}]}))
        with self.assertRaises(HeatmapError) as cm:
            load_heatmap(FS, path)
        self.assertIn("is not a heat map", str(cm.exception))
        write(path, "{nope")
        with self.assertRaises(HeatmapError):
            read_json(FS, path)

    def test_judgements_keep_only_valid_levels(self):
        path = os.path.join(self.dir, "judgement.json")
        write(path, json.dumps({"modules": {"m": {"business": {"level": 1, "reason": "core"}, "entropy": {"level": 7}}}}))
        judgement = load_judgement(FS, path)
        self.assertIsInstance(judgement, Judgement)
        self.assertEqual(list(judgement.modules["m"]), ["business"])

    def test_policy_sources(self):
        self.assertEqual(load_policy(FS, self.dir), (Policy(), "clean-eval default"))
        write(os.path.join(self.dir, ".clean-eval", "risk-model.json"), json.dumps({"unrated_band": "High"}))
        policy, source = load_policy(FS, self.dir)
        self.assertEqual((policy.unrated_band, source), ("High", os.path.join(".clean-eval", "risk-model.json")))
        write(os.path.join(self.dir, "bad.json"), "{nope")
        with self.assertRaises(InvalidPolicy):
            load_policy(FS, self.dir, os.path.join(self.dir, "bad.json"))


if __name__ == "__main__":
    unittest.main()
