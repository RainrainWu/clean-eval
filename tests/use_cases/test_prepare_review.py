"""Preparing the CI reviewer's context (use_cases/prepare_review.py): the policy of the PR's base commit, and one patch
per changed file with an index, through fakes of the Git and FileSystem interfaces."""
import unittest

from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, fake

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.policy import Policy
from clean_eval.domain.review import DiffIndex
from clean_eval.use_cases.interfaces import Git
from clean_eval.use_cases.prepare_review import policy_at, write_split_diff


class SplitDiffTest(unittest.TestCase):
    def test_one_patch_per_changed_file_and_an_index(self):
        git, fs = MemoryGit(), MemoryFileSystem()
        base = git.commit("init", {"src/a/old.py": "line\n" * 30, "src/a/keep.py": "k\n"})
        head = git.commit("change", {"src/a/keep.py": "k2\n", 'src/a/we"ird name.py': "w\n"}, remove=["src/a/old.py"])
        files = git.diff_files(base, head)
        index = write_split_diff(git, fs, "/ctx", base, head, files, {f: "src/a" for f in files})
        self.assertEqual([e.file for e in index], files)
        for e in index:
            self.assertTrue(fs.read_text("/ctx/" + e.patch).startswith("diff --git a/%s " % e.file), e)
            self.assertEqual((e.module, e.truncated), ("src/a", False))
        counts = {e.file: (e.added, e.removed) for e in index}
        self.assertEqual(counts, {"src/a/keep.py": (1, 1), "src/a/old.py": (0, 30), 'src/a/we"ird name.py': (1, 0)})
        self.assertEqual(DiffIndex.model_validate_json(fs.read_text("/ctx/diff/index.json")).root, index)

    def test_a_combined_diff_that_does_not_split_per_file_falls_back_to_one_diff_per_path(self):
        renamed = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
        per_path = {"new.py": "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n",
                    "old.py": "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"}
        git = fake(Git, diff=lambda base, head, path=None: renamed if path is None else per_path[path])
        index = write_split_diff(git, MemoryFileSystem(), "/ctx", "b", "h", ["new.py", "old.py"], {})
        self.assertEqual([(e.file, e.added, e.removed) for e in index], [("new.py", 1, 0), ("old.py", 0, 1)])


class PolicyAtBaseTest(unittest.TestCase):
    """A PR is judged by the policy of its base commit; a broken policy there falls back to the defaults, with a note."""

    def setUp(self):
        self.git = MemoryGit()

    def base_with(self, files):
        return self.git.commit("base", files)

    def test_no_policy_file_means_the_defaults(self):
        base = self.base_with({"a.py": "x = 1\n"})
        self.assertEqual(policy_at(self.git, base), (Policy(), "clean-eval default", "{}", None))

    def test_valid_overrides_at_the_base_are_applied(self):
        text = '{"unrated_band": "High", "business_weights": {"dependents": 0.4}}'
        base = self.base_with({"a.py": "x = 1\n", ".clean-eval/risk-model.json": text})
        policy, source, raw, note = policy_at(self.git, base)
        self.assertEqual((policy.unrated_band, policy.business_weights["dependents"]), ("High", 0.4))
        self.assertEqual((source, raw, note), (".clean-eval/risk-model.json@" + base[:8], text, None))

    def test_a_policy_changed_after_the_base_is_not_used(self):
        base = self.base_with({"a.py": "x = 1\n"})
        self.git.commit("weaken", {".clean-eval/risk-model.json": '{"unrated_band": "Low"}'})
        self.assertEqual(policy_at(self.git, base)[0], Policy())

    def test_a_git_failure_is_not_mistaken_for_no_policy(self):
        self.base_with({"a.py": "x = 1\n"})
        with self.assertRaises(HeatmapError):
            policy_at(self.git, "0" * 40)

    def test_invalid_or_v1_policy_falls_back_to_the_defaults_with_a_note(self):
        for text, expect in (('{"window_days": 30}', "v1 model"), ('{"effects_scale": 0}', "greater than 0"),
                             ("{not json", "not valid JSON")):
            base = self.base_with({"a.py": "x = 1\n", ".clean-eval/risk-model.json": text})
            policy, source, raw, note = policy_at(self.git, base)
            self.assertEqual((policy, source, raw), (Policy(), "clean-eval default", "{}"), text)
            self.assertTrue(note.startswith("ignored .clean-eval/risk-model.json at the base:"), note)
            self.assertIn(expect, note)


if __name__ == "__main__":
    unittest.main()
