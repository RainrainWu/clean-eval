"""The code-review-graph gateway (external/code_review_graph.py), against a fake CLI that writes the SQLite
database a build would."""
import os
import shutil
import tempfile
import unittest

from tests.helpers import make_executable, read

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.code_review_graph import CodeReviewGraphGateway

GRAPH_DB = """
    create table nodes (id integer primary key, kind text, qualified_name text, file_path text, language text, is_test integer);
    create table edges (kind text, source_qualified text, target_qualified text, confidence_tier text, target_resolution text);
    create table flows (id integer primary key, criticality real, entry_point_id integer);
    create table flow_memberships (flow_id integer, node_id integer);
    insert into nodes values (1, 'Function', '/r/src/a.py::f', '/r/src/a.py', 'python', 0), (2, 'Function', '/r/src/b.py::g', '/r/src/b.py', 'python', 0),
        (3, 'Test', '/r/tests/test_a.py::test_f', '/r/tests/test_a.py', 'python', 1), (4, 'Function', '/r/x.tf::r', '/r/x.tf', 'hcl', 0);
    insert into edges values ('CALLS', '/r/src/a.py::f', '/r/src/b.py::g', 'EXTRACTED', 'direct'),
        ('TESTED_BY', '/r/src/a.py::f', '/r/tests/test_a.py::test_f', 'EXTRACTED', null),
        ('CALLS', '/r/src/a.py::f', 'print', 'UNRESOLVED', 'unresolved');
    insert into flows values (1, 0.9, 1), (2, null, 2);
    insert into flow_memberships values (1, 1), (1, 2);
"""


def fake_code_review_graph(path, database=GRAPH_DB):
    """The code-review-graph CLI, as a fake: it records its arguments and writes the graph.db a build would (with
    `database` as its SQL; none when that is None)."""
    make_executable(path, "import pathlib, sqlite3, sys\n"
                          "data = pathlib.Path(sys.argv[sys.argv.index('--data-dir') + 1])\n"
                          "(data / 'args').write_text(' '.join(sys.argv[1:]))\n"
                          "sql = %r\n"
                          "if sql is not None:\n"
                          "    con = sqlite3.connect(data / 'graph.db')\n"
                          "    con.executescript(sql)\n"
                          "    con.commit()\n" % (database,))


class CodeReviewGraphGatewayTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.cli = os.path.join(self.dir, "code-review-graph")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_builds_then_reads_links_tests_and_flows_with_repo_relative_paths(self):
        fake_code_review_graph(self.cli)
        g = CodeReviewGraphGateway(self.cli).graph_of("/r", self.dir)
        self.assertEqual(read(os.path.join(self.dir, "args")), "build --repo /r --data-dir %s -q" % self.dir)
        self.assertEqual(g.links, {"src/a.py": {"src/b.py"}})
        self.assertEqual(g.tested, {"/r/src/a.py::f": {"tests/test_a.py"}})
        self.assertEqual(g.flows, [(0.9, "src/a.py", {"src/a.py", "src/b.py"}), (0.0, "src/b.py", set())])
        self.assertEqual((g.has_flows, g.stats["nodes"], g.stats["source"]), (True, 4, "code-review-graph"))
        self.assertNotIn("/r/x.tf::r", g.nodes)  # Python only

    def test_a_build_without_a_readable_database_fails(self):
        for database, expect in ((None, "no readable graph.db"), ("create table other (a integer);", "cannot read")):
            fake_code_review_graph(self.cli, database)
            if os.path.exists(os.path.join(self.dir, "graph.db")):
                os.remove(os.path.join(self.dir, "graph.db"))
            with self.assertRaises(HeatmapError) as cm:
                CodeReviewGraphGateway(self.cli).graph_of("/r", self.dir)
            self.assertIn(expect, str(cm.exception))

    def test_a_failing_or_missing_cli_fails(self):
        failing = os.path.join(self.dir, "failing")
        make_executable(failing, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
        with self.assertRaises(HeatmapError) as cm:
            CodeReviewGraphGateway(failing).graph_of("/r", self.dir)
        self.assertIn("boom", str(cm.exception))
        with self.assertRaises(HeatmapError) as cm:
            CodeReviewGraphGateway(os.path.join(self.dir, "missing")).graph_of("/r", self.dir)
        self.assertIn("install it", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
