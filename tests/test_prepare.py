import os
import shutil
import tempfile
import unittest

from helpers import Repo, days_ago, read

from clean_eval.adaptor.gateways.files import Folder
from clean_eval.adaptor.gateways.git_cli import Git
from clean_eval.domain.diff import split_patch
from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.policy import Policy
from clean_eval.use_cases.prepare_review import policy_at, write_split_diff


class SplitDiffTest(unittest.TestCase):
    def test_split_patch(self):
        full = "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n-2\n"
        self.assertEqual(split_patch(full), ["diff --git a/x b/x\n+1\n", "diff --git a/y b/y\n-2\n"])
        self.assertEqual(split_patch(""), [])

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
            index = write_split_diff(git, Folder(out), base, head, files, {f: "src/a" for f in files})
            self.assertEqual(len(index), len(files))
            for e in index:
                patch = read(os.path.join(out, e["patch"]))
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


class PolicyAtBaseTest(unittest.TestCase):
    """A PR is judged by the policy of its base commit; a broken policy there falls back to the defaults, with a note."""

    def setUp(self):
        self.repo = Repo()
        self.git = Git(self.repo.path)

    def tearDown(self):
        self.repo.cleanup()

    def base_with(self, files):
        return self.repo.commit("base", files, when=days_ago(2))

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
        self.repo.commit("weaken", {".clean-eval/risk-model.json": '{"unrated_band": "Low"}'}, when=days_ago(1))
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
