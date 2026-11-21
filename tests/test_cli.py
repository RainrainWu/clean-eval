"""The external layer end to end: `python3 -I clean_eval <command>` on a throwaway repository."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, Repo, days_ago, read

CE = os.path.join(ROOT, "clean_eval")


def run(repo, *args):
    return subprocess.run([sys.executable, "-I", CE, *args], cwd=repo, capture_output=True, text=True)


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = r = Repo()
        cls.base = r.commit("init", {
            "src/billing/core.py": "import requests\n\ndef charge(x):\n    return requests.post('u', json=x)\n" + "\n".join(
                "def f%d(a):\n    return a\n" % i for i in range(60)),
            "src/billing/models.py": "class Invoice:\n    pass\n" * 40,
            "src/users/api.py": "from src.billing.core import charge\n\ndef sign_up(u):\n    return charge(u)\n" * 30,
            "tests/test_core.py": "from src.billing.core import charge\n\ndef test_charge():\n    assert charge\n",
        }, when=days_ago(4))
        cls.head = r.commit("feat: users", {"src/users/api.py": "def sign_up(u):\n    return u\n", "docs/x.md": "doc\n",
                                            "infra/main.tf": "resource {}\n"}, when=days_ago(2))
        cls.out = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()
        shutil.rmtree(cls.out, ignore_errors=True)

    def test_rate_and_report(self):
        r = run(self.repo.path, "rate", "--depth", "2", "--out", self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(read(os.path.join(self.out, "data.json")))
        self.assertEqual(sorted(m["name"] for m in data["modules"]), ["src/billing", "src/users"])
        self.assertIn("Policy: clean-eval default", data["notes"])
        report = run(self.repo.path, "report", os.path.join(self.out, "data.json"))
        self.assertIn("| `src/", report.stdout)

    def test_the_readme_section_is_the_only_publishing_output(self):
        usage = run(self.repo.path, "--help").stdout
        self.assertIn("{rate,readme,report,assess,history,prepare,finalize,post,redact,guard}", usage)
        for gone in ("render", "diagram"):
            self.assertNotEqual(run(self.repo.path, gone, "x").returncode, 0, gone)

    def test_rate_a_past_revision(self):
        out = os.path.join(self.out, "rev")
        r = run(self.repo.path, "rate", "--rev", self.base, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(read(os.path.join(out, "data.json")))["ref"], self.base)
        bad = run(self.repo.path, "rate", "--rev", self.base, "--graph", "code-review-graph", "--out", out)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("only --graph builtin", bad.stderr)

    def test_readme_is_written_once_then_left_alone(self):
        repo = Repo()
        try:
            repo.commit("init", {"app/a.py": "def a():\n    return 1\n", "README.md": "# App\n"}, when=days_ago(2))
            out = os.path.join(self.out, "readme")
            self.assertEqual(run(repo.path, "rate", "--out", out).returncode, 0)
            status = os.path.join(out, "status.json")
            first = run(repo.path, "readme", os.path.join(out, "data.json"), "--status-json", status)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertTrue(json.loads(read(status))["readme_changed"])
            text = read(os.path.join(repo.path, "README.md"))
            self.assertTrue(text.startswith("# App\n\n## Risk Heat Map\n\n<!-- risk-heat-map:start -->"))
            self.assertIn("](docs/risk-heatmap.svg)", text)
            self.assertTrue(os.path.exists(os.path.join(repo.path, "docs", "risk-heatmap.svg")))
            again = run(repo.path, "readme", os.path.join(out, "data.json"), "--status-json", status)
            self.assertIn("unchanged", again.stdout)
            self.assertFalse(json.loads(read(status))["readme_changed"])
        finally:
            repo.cleanup()

    def test_assess_a_change_from_its_base(self):
        r = run(self.repo.path, "assess", "--base", self.base, "--head", self.head)
        self.assertEqual(r.returncode, 0, r.stderr)
        risk = json.loads(r.stdout)
        self.assertEqual(risk["files"]["src/users/api.py"], "src/users")
        self.assertEqual(risk["ignored"], ["docs/x.md"])
        mods = {m["module"]: m for m in risk["modules"]}
        self.assertFalse(mods["infra"]["rated"])
        self.assertEqual(mods["infra"]["band"], "Moderate")
        self.assertIn(risk["max_band"], ("Moderate", "High", "Critical"))

    def test_invalid_policy_is_reported_not_raised(self):
        repo = Repo()
        try:
            repo.commit("init", {"a/b.py": "x = 1\n", ".clean-eval/risk-model.json": '{"window_days": 30}'}, when=days_ago(1))
            r = run(repo.path, "rate", "--out", os.path.join(self.out, "bad"))
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("v1 model", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        finally:
            repo.cleanup()


if __name__ == "__main__":
    unittest.main()
