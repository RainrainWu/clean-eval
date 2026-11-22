"""The sources a rating reads (use_cases/sources.py): the tracked Python files of a working tree as they are on disk,
through the Git and FileSystem interfaces, or of a revision, from git objects."""
import unittest

from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, blob_id

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.sources import GENERATED, MAX_FILE_BYTES, content_id, count_loc, read_revision, read_working_tree

P = Policy()


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.git = git = MemoryGit()
        self.old = git.commit("init", {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"})
        git.commit("change", {"src/a.py": "def f():\n    return 2\n", "src/x_pb2.py": "X = 1\n", "src/gone.py": "G = 1\n",
                              "src/huge.py": "#\n" * (MAX_FILE_BYTES // 2 + 1), "docs/n.md": "n\n"})
        git.stage("src/link.py")
        self.fs = MemoryFileSystem({"/repo/" + p: t for p, t in git.commits[-1]["tree"].items() if p != "src/gone.py"})
        self.fs.write_text("/repo/src/a.py", "def f():\n    return 3\n")  # edited, not committed
        self.fs.write_text("/repo/src/untracked.py", "U = 1\n")
        self.fs.link("/repo/src/link.py", "/etc/hosts")

    def test_a_working_tree_is_its_tracked_python_files_as_they_are_on_disk(self):
        snap, read = read_working_tree(self.git, self.fs, P)
        files = {f.path: f for f in read}
        self.assertEqual(sorted(files), ["src/a.py", "tests/test_a.py"])  # not generated, linked, gone, huge or untracked
        self.assertEqual(files["src/a.py"].text, "def f():\n    return 3\n")
        self.assertEqual(files["src/a.py"].blob, blob_id("def f():\n    return 3\n"))  # git's id: caches are shared
        self.assertTrue(files["tests/test_a.py"].is_test)
        self.assertEqual((snap.repo, snap.ref, snap.sha, snap.root), ("demo", "main", self.git.rev_parse("HEAD", True), "/repo"))

    def test_a_revision_is_read_from_git_objects_sharing_their_contents(self):
        blobs = {}
        snap, old = read_revision(self.git, self.old, P, blobs)
        self.assertEqual({f.path: f.text for f in old},
                         {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"})
        self.assertEqual((snap.ref, snap.sha, len(blobs)), (self.old, self.old[:7], 2))
        _, head = read_revision(self.git, "HEAD", P, blobs)
        self.assertEqual(sorted(f.path for f in head), ["src/a.py", "src/gone.py", "tests/test_a.py"])  # not generated or huge
        self.assertEqual(len(blobs), 4)  # the unchanged test file is read once

    def test_helpers(self):
        self.assertEqual(count_loc("a = 1\n\n# note\n  \nb = 2\n"), 2)
        self.assertTrue(all(GENERATED.search("/" + p) for p in ("x_pb2.py", "a/generated/b.py", "vendor/x.py")))
        self.assertEqual(content_id("hi\n"), "45b983be36b73c0788dc9cbcb76cbb80fc7bb057")  # `git hash-object` of "hi\n"


if __name__ == "__main__":
    unittest.main()
