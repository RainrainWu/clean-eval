import os
import shutil
import tempfile
import unittest

from helpers import Repo, days_ago, read, write
import prepare

from clean_eval.adaptor.gateways.git_cli import Git


class SplitDiffTest(unittest.TestCase):
    def test_split_patch(self):
        full = "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n-2\n"
        self.assertEqual(prepare.split_patch(full), ["diff --git a/x b/x\n+1\n", "diff --git a/y b/y\n-2\n"])
        self.assertEqual(prepare.split_patch(""), [])

    def test_write_split_diff_with_rename_and_odd_names(self):
        r = Repo()
        out = tempfile.mkdtemp()
        try:
            base = r.commit("init", {"src/a/old.py": "line\n" * 30, "src/a/keep.py": "k\n"}, when=days_ago(3))
            r.git("mv", "src/a/old.py", "src/a/new.py")
            r.write('src/a/we"ird name.py', "w\n")
            r.write("src/a/keep.py", "k2\n")
            head = r.commit("change", when=days_ago(2))
            git = Git(r.path)
            files = git.diff_files(base, head)
            index = prepare.write_split_diff(git, base, head, files, {f: "src/a" for f in files}, out)
            self.assertEqual(len(index), len(files))
            for e in index:
                patch = read(os.path.join(out, os.path.basename(e["patch"])))
                self.assertTrue(patch.startswith("diff --git "), e)
                self.assertEqual(e["module"], "src/a")
                self.assertFalse(e["truncated"])
            by_file = {e["file"]: e for e in index}
            self.assertEqual(by_file["src/a/keep.py"]["added"], 1)
            self.assertEqual(by_file["src/a/keep.py"]["removed"], 1)
            self.assertIn('src/a/we"ird name.py', by_file)
        finally:
            r.cleanup()
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
