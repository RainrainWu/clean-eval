"""The external layer's gateways, each against the real dependency it encapsulates (git, the file system, SQLite, the
environment, the clock) or a fake executable standing in for it (gh, code-review-graph)."""
import datetime as dt
import os
import shutil
import sqlite3
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
from clean_eval.external.sqlite import SqliteGateway


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


class SqliteGatewayTest(unittest.TestCase):
    def test_reads_and_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "x.db")
            con = sqlite3.connect(db)
            con.executescript("create table t (a integer); insert into t values (1), (2);")
            con.close()
            with SqliteGateway(db) as database:
                self.assertEqual(database.rows("select a from t where a > ?", (1,)), [(2,)])
                with self.assertRaises(HeatmapError):  # opened read-only
                    database.rows("insert into t values (3)")
        with self.assertRaises(HeatmapError):
            SqliteGateway("/nonexistent/graph.db")


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


class CliGatewaysTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_code_review_graph_build(self):
        cli = os.path.join(self.dir, "code-review-graph")
        make_executable(cli, "import sys, pathlib\npathlib.Path(sys.argv[sys.argv.index('--data-dir') + 1], 'args').write_text("
                             "' '.join(sys.argv[1:]))\n")
        CodeReviewGraphGateway(cli).build("/repo", self.dir)
        self.assertEqual(read(os.path.join(self.dir, "args")), "build --repo /repo --data-dir %s -q" % self.dir)
        failing = os.path.join(self.dir, "failing")
        make_executable(failing, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
        with self.assertRaises(HeatmapError) as cm:
            CodeReviewGraphGateway(failing).build("/repo", self.dir)
        self.assertIn("boom", str(cm.exception))
        with self.assertRaises(HeatmapError) as cm:
            CodeReviewGraphGateway(os.path.join(self.dir, "missing")).build("/repo", self.dir)
        self.assertIn("install it", str(cm.exception))

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
