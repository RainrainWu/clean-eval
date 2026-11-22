"""The git gateway (external/git.py), against real throwaway repositories; and the contract it shares with the use
cases' MemoryGit (tests/use_cases/fakes.py): the same checks run against both, over the same history."""
import datetime as dt
import os
import unittest

from tests.helpers import Repo, days_ago
from tests.use_cases.fakes import MemoryGit, blob_id

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.git import GitGateway


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


FIRST, SECOND = dt.datetime(2026, 3, 1, 12, tzinfo=dt.timezone.utc), dt.datetime(2026, 3, 2, 12, tzinfo=dt.timezone.utc)


class GitContract:
    """Two commits: src/a.py and docs/n.md, then src/a.py changed, src/b.py added and docs/n.md removed."""

    def setUp(self):
        self.git, self.first, self.second = self.build()

    def test_revisions(self):
        self.assertEqual(len(self.git.rev_parse("HEAD")), 40)
        self.assertEqual(self.git.rev_parse("HEAD", short=True), self.second[:7])
        self.assertEqual(self.git.rev_parse(self.second[:10]), self.second)
        self.assertTrue(self.git.has_commit(self.first))
        self.assertFalse(self.git.has_commit("0" * 40))
        self.assertEqual(self.git.merge_base(self.first, self.second), self.first)
        self.assertEqual((self.git.branch(), self.git.default_branch()), ("main", "main"))
        with self.assertRaises(HeatmapError):
            self.git.ensure_commit("0" * 40, "refs/heads/nope")

    def test_files_and_contents(self):
        self.assertEqual(self.git.ls_files(), ["src/a.py", "src/b.py"])
        self.assertEqual(self.git.ls_tree(self.first), [("docs/n.md", blob_id("n\n"), 2), ("src/a.py", blob_id("a\n"), 2)])
        self.assertEqual(self.git.read_blobs([blob_id("a\n"), blob_id("a2\n"), "0" * 40]),
                         {blob_id("a\n"): "a\n", blob_id("a2\n"): "a2\n"})  # an unknown blob is left out
        self.assertEqual(self.git.show(self.second, "src/b.py"), "b\n")
        self.assertIsNone(self.git.show(self.first, "src/b.py"))  # not there at that revision
        self.assertIsNone(self.git.show(self.second, "src"))      # a folder is not a file
        with self.assertRaises(HeatmapError):                     # a bad revision is not "not there"
            self.git.show("0" * 40, "src/a.py")

    def test_changes(self):
        self.assertEqual(self.git.diff_files(self.first, self.second), ["docs/n.md", "src/a.py", "src/b.py"])
        patch = self.git.diff(self.first, self.second, "src/a.py")
        self.assertTrue(patch.startswith("diff --git a/src/a.py b/src/a.py\n"))
        self.assertIn("\n-a\n+a2\n", patch)
        self.assertEqual(self.git.diff(self.first, self.second).count("diff --git "), 3)
        self.assertIn("+b", self.git.commit_patch(self.second, ["src/b.py"]))

    def test_history(self):
        log = self.git.first_parent_log("main", "2026-02-28", "2026-03-03")
        self.assertEqual([(sha, parents, author, ts, subject) for sha, parents, author, ts, subject in log],
                         [(self.second, [self.first], "Test", int(SECOND.timestamp()), "feat: b (#2)"),
                          (self.first, [], "Test", int(FIRST.timestamp()), "init")])
        self.assertEqual(self.git.first_parent_log("main", "2026-03-10", "2026-03-20"), [])
        lines = self.git.file_log(self.second, "src/a.py").splitlines()
        self.assertEqual([line.split()[0] for line in lines], [self.second[:7], self.first[:7]])  # newest first
        self.assertTrue(lines[0].endswith("feat: b (#2)"))


class MemoryGitContract(GitContract, unittest.TestCase):
    def build(self):
        git = MemoryGit()
        first = git.commit("init", {"src/a.py": "a\n", "docs/n.md": "n\n"}, when=FIRST, author="Test")
        second = git.commit("feat: b (#2)", {"src/a.py": "a2\n", "src/b.py": "b\n"}, when=SECOND, author="Test",
                            remove=["docs/n.md"])
        return git, first, second


class GitGatewayContract(GitContract, unittest.TestCase):
    def build(self):
        self.repo = Repo()
        self.addCleanup(self.repo.cleanup)
        first = self.repo.commit("init", {"src/a.py": "a\n", "docs/n.md": "n\n"}, when=FIRST)
        os.remove(os.path.join(self.repo.path, "docs", "n.md"))
        second = self.repo.commit("feat: b (#2)", {"src/a.py": "a2\n", "src/b.py": "b\n"}, when=SECOND)
        return GitGateway(self.repo.path), first, second


if __name__ == "__main__":
    unittest.main()
