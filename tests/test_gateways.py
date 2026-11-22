"""The external layer's gateways, each against the real dependency it encapsulates (git, the file system, the
environment, the clock) or a fake executable standing in for it (gh, code-review-graph and the SQLite database it
writes)."""
import datetime as dt
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import FakeBin, Repo, days_ago, make_executable, read, write

from clean_eval.domain.errors import HeatmapError, RemoteError
from clean_eval.external.clock import ClockGateway
from clean_eval.external.code_review_graph import CodeReviewGraphGateway
from clean_eval.external.environment import EnvironmentGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.external.github import GitHubGateway


class GitGatewayTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo()

    def tearDown(self):
        self.repo.cleanup()

    def test_diff_show_and_odd_names(self):
        base = self.repo.commit("init", {"a.py": "1\n"}, when=days_ago(2))
        head = self.repo.commit("odd names", {"src/résumé.py": "2\n", 'src/we"ird.py': "3\n"}, when=days_ago(1))
        git = GitGateway(self.repo.path)
        self.assertEqual(sorted(git.diff_files(base, head)), sorted(["src/résumé.py", 'src/we"ird.py']))
        self.assertEqual(git.show(head, "src/résumé.py"), "2\n")
        self.assertIsNone(git.show(base, "src/résumé.py"))      # not there at that revision
        self.assertIsNone(git.show(head, "src"))                  # a folder is not a file
        with self.assertRaises(HeatmapError):                     # a real failure is not "not there"
            git.show("0" * 40, "a.py")
        self.repo.git("remote", "add", "origin", "git@github.com:org/My-Repo.git")
        self.assertEqual(git.repo_name(), "My-Repo")

    def test_history_reads(self):
        first = self.repo.commit("init", {"a.py": "1\n"}, when=days_ago(3))
        second = self.repo.commit("feat: b (#2)", {"b.py": "2\n"}, when=days_ago(2))
        git = GitGateway(self.repo.path)
        log = git.first_parent_log("main", days_ago(10).date().isoformat(), days_ago(0).date().isoformat())
        self.assertEqual([(sha, parents, subject) for sha, parents, _, _, subject in log],
                         [(second, [first], "feat: b (#2)"), (first, [], "init")])
        self.assertEqual(git.default_branch(), "main")  # no origin: the local main
        self.assertTrue(git.has_commit(first))
        self.assertFalse(git.has_commit("0" * 40))
        with self.assertRaises(HeatmapError):            # not here, and no origin to fetch it from
            git.ensure_commit("0" * 40, "refs/heads/nope")
        self.assertIn("feat: b (#2)", git.file_log(second, "b.py", 10))
        self.assertIn("+2", git.commit_patch(second, ["b.py"]))

    def test_commands_are_not_composed_outside(self):
        self.assertFalse(hasattr(GitGateway, "run"))  # callers ask for operations, not for git command lines


class FileSystemGatewayTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.fs = FileSystemGateway()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def test_reading_and_writing(self):
        self.fs.write_text(self.path("a", "b", "c.txt"), "hi")            # folders are made
        self.assertEqual(self.fs.read_text(self.path("a", "b", "c.txt")), "hi")
        with open(self.path("bad.txt"), "wb") as f:
            f.write(b"ok \xff\xfe")
        self.assertEqual(self.fs.read_text(self.path("bad.txt")), "ok ��")  # undecodable bytes replaced
        with self.assertRaises(HeatmapError):
            self.fs.read_text(self.path("missing.txt"))
        self.fs.append_text(self.path("out.txt"), "a=1\n")
        self.fs.append_text(self.path("out.txt"), "b=2\n")
        self.assertEqual(read(self.path("out.txt")), "a=1\nb=2\n")

    def test_listing_copying_and_comparing(self):
        write(self.path("t", "x.py"), "x")
        write(self.path("t", "sub", "y.py"), "y")
        os.symlink(self.path("t", "sub"), self.path("t", "linked"))     # a symlinked folder is neither followed nor listed
        self.assertEqual(sorted(self.fs.files_under(self.path("t"))), sorted(["x.py", os.path.join("sub", "y.py")]))
        self.assertEqual(self.fs.files_under(self.path("nope")), [])
        self.fs.copy_tree(self.path("t"), self.path("copy"))
        self.assertTrue(self.fs.is_link(self.path("copy", "linked")))  # links stay links
        self.assertTrue(self.fs.same_content(self.path("t", "x.py"), self.path("copy", "x.py")))
        self.assertEqual(self.fs.glob(self.path("t"), "*.py"), [self.path("t", "x.py")])
        self.fs.remove_tree(self.path("copy"))
        self.assertFalse(self.fs.exists(self.path("copy")))

    def test_resolving_links(self):
        write(self.path("target.txt"), "t")
        os.symlink(self.path("target.txt"), self.path("link.txt"))
        self.assertEqual(self.fs.real_path(self.path("link.txt")), self.path("target.txt"))
        self.fs.remove(self.path("link.txt"))                            # the link goes, its target stays
        self.assertEqual((self.fs.lexists(self.path("link.txt")), self.fs.exists(self.path("target.txt"))), (False, True))

    def test_finding_executables(self):
        bin_dir = self.path("bin")
        os.makedirs(bin_dir)
        make_executable(os.path.join(bin_dir, "tool"), "print(1)\n")
        self.assertEqual(self.fs.find_executable("tool", bin_dir), os.path.join(bin_dir, "tool"))
        self.assertIsNone(self.fs.find_executable("tool", self.path("empty")))


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


class EnvironmentAndClockTest(unittest.TestCase):
    def test_environment(self):
        env = EnvironmentGateway()
        with mock.patch.dict(os.environ, {"CE_ROOTS": os.pathsep.join(["/a", "", "/b c"]), "CE_X": "1"}):
            self.assertEqual(env.paths("CE_ROOTS"), ["/a", "/b c"])
            self.assertEqual(env.paths("CE_UNSET"), [])
            self.assertEqual((env.get("CE_X"), env.get("CE_UNSET", "d")), ("1", "d"))
            snapshot = env.variables()
        snapshot["CE_X"] = "changed"
        self.assertNotEqual(os.environ.get("CE_X"), "changed")  # a copy, not the live environment

    def test_clock(self):
        self.assertEqual(ClockGateway().today(), dt.date.today().isoformat())


class GitHubGatewayTest(unittest.TestCase):
    def test_github_validates_what_it_reads(self):
        fake = FakeBin()
        try:
            pr = {"number": 7, "title": "t", "baseRefName": "main", "headRefName": "f", "baseRefOid": "a" * 40,
                  "headRefOid": "b" * 40, "author": {"login": "dev"}}
            with mock.patch.dict(os.environ, fake.env()):
                fake.gh({"pr_view": {"out": pr}})
                self.assertEqual(GitHubGateway().pull_request(7).author, "dev")
                fake.gh({"pr_view": {"out": {**pr, "headRefOid": "--upload-pack=x"}}})
                with self.assertRaises(RemoteError):  # never handed on to git
                    GitHubGateway().pull_request(7)
        finally:
            fake.cleanup()


if __name__ == "__main__":
    unittest.main()
