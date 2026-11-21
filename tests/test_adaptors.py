"""Adaptors: the gateways against real temporary repositories, files and databases, and the presenters."""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest

from helpers import Repo, days_ago, facts, heatmap, rating, read, sf, write

from clean_eval.adaptor.gateways.ast_graph import BuiltinGraph
from clean_eval.adaptor.gateways.code_review_graph import read_graph_db
from clean_eval.adaptor.gateways.coverage_xml import CoverageXmlReader
from clean_eval.adaptor.gateways.files import FileReadmeStore, Workspace, load_policy
from clean_eval.adaptor.gateways.git_cli import Git, unquote_path
from clean_eval.adaptor.gateways.git_source import GENERATED, RevisionSource, WorkingTreeSource, content_id, count_loc
from clean_eval.adaptor.gateways.python_signals import PythonSignals, python_stats, write_calls
from clean_eval.adaptor.presenters import style
from clean_eval.adaptor.presenters.diagram import SvgDiagramPresenter
from clean_eval.adaptor.presenters.html_page import HtmlPagePresenter
from clean_eval.adaptor.presenters.markdown_report import MarkdownReportPresenter
from clean_eval.adaptor.presenters.treemap import squarify
from clean_eval.domain.entities import Snapshot
from clean_eval.domain.errors import HeatmapError, InvalidPolicy
from clean_eval.domain.policy import ASPECTS, BANDS, Policy
from clean_eval.domain.readme import MARKERS

P = Policy()
SAMPLE = '''
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
'''


class PythonSignalsTest(unittest.TestCase):
    def test_python_stats(self):
        st = python_stats(SAMPLE)
        self.assertEqual((len(st.funcs), st.typed, st.broad, st.swallow), (2, 1, 1, 1))
        self.assertEqual(st.funcs[0][1], 3)  # one if and one boolean operator on top of the base path
        self.assertIsNone(python_stats("def broken(:"))

    def test_write_calls_are_counted_with_a_line_and_gated_by_context(self):
        aws = "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"
        self.assertEqual(write_calls(aws)["aws_write"], (2, 3))
        self.assertNotIn("aws_write", write_calls("x.put_object(1)\n"))  # no boto import: not an AWS write

    def test_signals_are_cached_per_content(self):
        signals = PythonSignals()
        a = signals.file_signals(sf("a.py", 1, "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", blob="b1"))
        self.assertEqual(a.todo, 2)
        self.assertIs(signals.file_signals(sf("renamed.py", 1, "ignored", blob="b1")), a)


class GitSourcesTest(unittest.TestCase):
    def setUp(self):
        self.repo = r = Repo()
        self.old = r.commit("init", {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"}, when=days_ago(3))
        r.commit("change", {"src/a.py": "def f():\n    return 2\n\n\ndef g():\n    pass\n", "src/x_pb2.py": "X = 1\n"}, when=days_ago(2))
        write(os.path.join(r.path, "src", "untracked.py"), "U = 1\n")
        os.symlink("/etc/hosts", os.path.join(r.path, "src", "link.py"))
        r.git("add", "src/link.py")

    def tearDown(self):
        self.repo.cleanup()

    def test_working_tree_reads_tracked_python_only(self):
        src = WorkingTreeSource(Git(self.repo.path), P)
        files = {f.path: f for f in src.read()}
        self.assertEqual(sorted(files), ["src/a.py", "tests/test_a.py"])  # no untracked, generated or symlinked file
        self.assertTrue(files["tests/test_a.py"].is_test)
        self.assertEqual(files["src/a.py"].blob, self.repo.git("rev-parse", "HEAD:src/a.py"))  # same id as git
        snap = src.snapshot()
        self.assertEqual((snap.ref, snap.sha), ("main", self.repo.git("rev-parse", "--short", "HEAD")))

    def test_revision_reads_git_objects_and_shares_contents(self):
        cache = {}
        old = {f.path: f for f in RevisionSource(Git(self.repo.path), self.old, P, cache).read()}
        self.assertEqual(old["src/a.py"].text, "def f():\n    return 1\n")
        self.assertEqual(len(cache), 2)
        RevisionSource(Git(self.repo.path), "HEAD", P, cache).read()
        self.assertEqual(len(cache), 3)  # the unchanged test file is read once

    def test_helpers(self):
        self.assertEqual(count_loc("a = 1\n\n# note\n  \nb = 2\n"), 2)
        self.assertTrue(all(GENERATED.search("/" + p) for p in ("x_pb2.py", "a/generated/b.py", "vendor/x.py")))
        self.assertEqual(content_id("hi\n"), subprocess.run(["git", "hash-object", "--stdin"], input="hi\n", capture_output=True,
                                                            text=True).stdout.strip())


class GitCliTest(unittest.TestCase):
    def test_unquote_path(self):
        self.assertEqual(unquote_path("src/a.py"), "src/a.py")
        self.assertEqual(unquote_path('"src/r\\303\\251sum\\303\\251.py"'), "src/résumé.py")
        self.assertEqual(unquote_path('"src/we\\"ird\\\\x.py"'), 'src/we"ird\\x.py')

    def test_diff_show_and_names(self):
        r = Repo()
        try:
            base = r.commit("init", {"a.py": "1\n"}, when=days_ago(2))
            head = r.commit("odd names", {"src/résumé.py": "2\n", 'src/we"ird.py': "3\n"}, when=days_ago(1))
            git = Git(r.path)
            self.assertEqual(sorted(git.diff_files(base, head)), sorted(["src/résumé.py", 'src/we"ird.py']))
            self.assertEqual(git.show(head, "src/résumé.py"), "2\n")
            self.assertIsNone(git.show(base, "src/résumé.py"))
            r.git("remote", "add", "origin", "git@github.com:org/My-Repo.git")
            self.assertEqual(git.repo_name(), "My-Repo")
        finally:
            r.cleanup()


def snap(root="/r"):
    return Snapshot("demo", "main", "abc", root)


class BuiltinGraphTest(unittest.TestCase):
    FILES = {
        "src/pkg/__init__.py": "from .core import run\n",
        "src/pkg/core.py": "import json\nfrom . import util\n\ndef run():\n    return util.helper()\n\nclass Svc:\n    def go(self):\n        pass\n",
        "src/pkg/util.py": "def helper():\n    return 1\n",
        "tools/json.py": "X = 1\n",
        "lambdas/x/handler.py": "import utils\n\ndef handler(e, c):\n    return utils.f()\n",
        "lambdas/x/utils.py": "def f():\n    pass\n",
        "other/deep/utils.py": "def f():\n    pass\n",
        "tests/test_core.py": "from pkg import run\nfrom pkg.core import Svc\n\ndef test_run():\n    assert run() and Svc().go() is None\n",
    }

    def graph(self, builder=None):
        files = [sf(p, 1, t, test=p.startswith("tests/"), blob="blob-" + p) for p, t in self.FILES.items()]
        return (builder or BuiltinGraph()).graph_for(snap(), files)

    def test_imports_resolve_by_path(self):
        g = self.graph()
        self.assertEqual(g.links["src/pkg/core.py"], {"src/pkg/util.py"})              # relative; stdlib json skipped
        self.assertEqual(g.links["src/pkg/__init__.py"], {"src/pkg/core.py"})
        self.assertEqual(g.links["tests/test_core.py"], {"src/pkg/__init__.py", "src/pkg/core.py"})  # src layout
        self.assertEqual(g.links["lambdas/x/handler.py"], {"lambdas/x/utils.py"})      # sibling, not other/deep
        self.assertFalse(g.has_flows)
        self.assertEqual(g.stats["source"], "built-in ast")

    def test_tests_link_to_the_functions_they_import_and_name(self):
        g = self.graph()
        self.assertEqual(g.tested["src/pkg/core.py::run"], {"tests/test_core.py"})      # through the __init__ re-export
        self.assertEqual(g.tested["src/pkg/core.py::Svc.go"], {"tests/test_core.py"})
        self.assertNotIn("src/pkg/util.py::helper", g.tested)
        self.assertEqual(g.nodes["src/pkg/core.py::Svc.go"], ("Function", "src/pkg/core.py", False))

    def test_per_file_syntax_is_cached(self):
        builder = BuiltinGraph()
        self.graph(builder)
        before = dict(builder._cache)
        self.graph(builder)
        self.assertEqual(builder._cache, before)


class CodeReviewGraphTest(unittest.TestCase):
    def test_reads_links_tests_and_flows_with_repo_relative_paths(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "graph.db")
            con = sqlite3.connect(db)
            con.executescript("""
                create table nodes (id integer primary key, kind text, qualified_name text, file_path text, language text, is_test integer);
                create table edges (kind text, source_qualified text, target_qualified text, confidence_tier text, target_resolution text);
                create table flows (id integer primary key, criticality real, entry_point_id integer);
                create table flow_memberships (flow_id integer, node_id integer);
                insert into nodes values (1, 'Function', '/r/src/a.py::f', '/r/src/a.py', 'python', 0), (2, 'Function', '/r/src/b.py::g', '/r/src/b.py', 'python', 0),
                    (3, 'Test', '/r/tests/test_a.py::test_f', '/r/tests/test_a.py', 'python', 1), (4, 'Function', '/r/x.tf::r', '/r/x.tf', 'hcl', 0);
                insert into edges values ('CALLS', '/r/src/a.py::f', '/r/src/b.py::g', 'EXTRACTED', 'direct'),
                    ('TESTED_BY', '/r/src/a.py::f', '/r/tests/test_a.py::test_f', 'EXTRACTED', null),
                    ('CALLS', '/r/src/a.py::f', 'print', 'UNRESOLVED', 'unresolved');
                insert into flows values (1, 0.9, 1);
                insert into flow_memberships values (1, 1), (1, 2);
            """)
            con.commit()
            con.close()
            g = read_graph_db(db, "/r")
        self.assertEqual(g.links, {"src/a.py": {"src/b.py"}})
        self.assertEqual(g.tested, {"/r/src/a.py::f": {"tests/test_a.py"}})
        self.assertEqual(g.flows, [(0.9, "src/a.py", {"src/a.py", "src/b.py"})])
        self.assertTrue(g.has_flows)
        self.assertNotIn("/r/x.tf::r", g.nodes)  # Python only
        with self.assertRaises(HeatmapError):
            read_graph_db("/nonexistent/graph.db", "/r")


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_coverage_report_line_rates(self):
        self.assertEqual(CoverageXmlReader().line_rates(snap(self.dir)), {})
        write(os.path.join(self.dir, "coverage.xml"), '<coverage><class name="a" filename="src/a.py" line-rate="0.75"/></coverage>')
        self.assertEqual(CoverageXmlReader().line_rates(snap(self.dir)), {"src/a.py": 0.75})

    def test_workspace_round_trips_the_heatmap_with_facts(self):
        ws, path = Workspace(), os.path.join(self.dir, "out", "data.json")
        h = heatmap(rating("a", 10, fact=facts(name="a")), rating("b", 20, unverified=None))
        ws.save_heatmap(h, path)
        self.assertEqual(ws.load_heatmap(path), h)
        with self.assertRaises(HeatmapError):
            ws.load_heatmap(os.path.join(self.dir, "missing.json"))

    def test_policy_sources(self):
        self.assertEqual(load_policy(self.dir), (Policy(), "clean-eval default"))
        write(os.path.join(self.dir, ".clean-eval", "risk-model.json"), json.dumps({"unrated_band": "High"}))
        policy, source = load_policy(self.dir)
        self.assertEqual((policy.unrated_band, source), ("High", os.path.join(".clean-eval", "risk-model.json")))
        write(os.path.join(self.dir, "bad.json"), "{nope")
        with self.assertRaises(InvalidPolicy):
            load_policy(self.dir, os.path.join(self.dir, "bad.json"))

    def test_readme_store_links_relative_to_the_readme(self):
        root = FileReadmeStore(os.path.join(self.dir, "README.md"), os.path.join(self.dir, "docs", "risk-heatmap.svg"))
        self.assertEqual(root.image_link(), "docs/risk-heatmap.svg")
        nested = FileReadmeStore(os.path.join(self.dir, "docs", "README.md"), os.path.join(self.dir, "docs", "risk-heatmap.svg"))
        self.assertEqual(nested.image_link(), "risk-heatmap.svg")
        self.assertIsNone(root.read_readme())
        root.write("# R", "<svg/>")
        self.assertEqual((read(os.path.join(self.dir, "README.md")), root.svg_exists()), ("# R\n", True))


class Presenters(unittest.TestCase):
    def mixed(self):
        return heatmap(rating("src/api", 3000, 90, 80, 30, 70), rating("src/<x>&y|z", 500, 10, 5, 5, 20),
                       rating("terraform/lambda", 700, 20, 20, 10, None))

    def test_squarify_fills_the_canvas_without_loss(self):
        rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
        self.assertEqual(len(rects), 3)
        self.assertAlmostEqual(sum(w * h for _, _, _, w, h in rects), 10000.0)

    def test_page_escapes_script_close_sets_title_and_drops_facts(self):
        h = heatmap(rating(fact=facts()))
        h.modules[0].evidence["biz"] = ["</script><b>"]
        html = HtmlPagePresenter("<title>__TITLE__</title><style>:root{/*__BAND_VARS__*/}</style><script>__DATA__</script>").present(h, " Detail")
        self.assertIn("<title>demo Risk Heat Map Detail</title>", html)
        self.assertNotIn("</script><b>", html)
        self.assertNotIn('"facts"', html)
        self.assertIn("--crit: %s; --crit-rgb: 208,59,59;" % style.BAND_STYLE["Critical"][0], html)
        for label in [a[2] for a in ASPECTS] + [name for _, name in BANDS]:
            self.assertIn(label, html)

    def test_svg_has_one_tile_per_module_and_escapes_names(self):
        svg = SvgDiagramPresenter().svg(self.mixed())
        self.assertEqual(svg.count("<rect "), 4)  # three tiles and the backing
        self.assertIn("src/&lt;x&gt;&amp;y|z", svg)
        self.assertNotIn("<x>", svg)
        self.assertEqual(svg.count('fill-opacity="0.35"'), 3)
        self.assertIn('fill="#0d1117"', svg)

    def test_readme_block_has_markers_fingerprint_image_and_legend(self):
        block = SvgDiagramPresenter().readme_block(self.mixed(), "docs/risk-heatmap.svg", "0123456789ab")
        self.assertTrue(block.startswith(MARKERS[0]) and block.rstrip().endswith(MARKERS[1]))
        self.assertIn("<!-- risk-heat-map:fingerprint=0123456789ab -->", block)
        self.assertIn("![Risk heat map of demo at abc1234](docs/risk-heatmap.svg)", block)
        self.assertIn("🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100", block)
        self.assertNotIn("## ", block)  # the section heading lives outside the markers

    def test_markdown_report_ranks_and_escapes(self):
        text = MarkdownReportPresenter().report(self.mixed())
        rows = [l for l in text.splitlines() if l.startswith("| `")]
        self.assertTrue(rows[0].startswith("| `src/api`"))
        self.assertIn("`src/<x>&y\\|z`", text)
        self.assertIn("⚪ N/A", text)
        empty = heatmap()
        empty.modules = []
        self.assertIn("No Python source", MarkdownReportPresenter().report(empty))


if __name__ == "__main__":
    unittest.main()
