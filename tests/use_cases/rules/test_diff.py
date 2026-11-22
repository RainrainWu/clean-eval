"""Diffs (use_cases/rules/diff.py): a combined patch split into one per file."""
import unittest

from clean_eval.use_cases.rules.diff import split_patch


class SplitDiffTest(unittest.TestCase):
    def test_split_patch(self):
        full = "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n-2\n"
        self.assertEqual(split_patch(full), ["diff --git a/x b/x\n+1\n", "diff --git a/y b/y\n-2\n"])
        self.assertEqual(split_patch(""), [])


if __name__ == "__main__":
    unittest.main()
