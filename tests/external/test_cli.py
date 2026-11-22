"""The command line end to end: `python -I clean_eval <command>` (the environment uv.lock pins) on throwaway repositories."""

import datetime as dt
import json
import os
import subprocess
import sys

import pytest

from tests.helpers import ROOT, Repo, days_ago, read, write_json

pytestmark = pytest.mark.e2e  # every test runs `python -I clean_eval ...` in a subprocess

CE = os.path.join(ROOT, "clean_eval")


def run(repo, *args):
    return subprocess.run([sys.executable, "-I", CE, *args], cwd=repo, capture_output=True, text=True)


D = lambda s: dt.datetime.fromisoformat(s + "T12:00:00+00:00")  # noqa: E731


class TestCli:
    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    def repository(cls, tmp_path_factory):
        """A repository with two commits, and a folder for the commands' output (made once for the class)."""
        cls.repo = r = Repo(tmp_path_factory.mktemp("repo"))
        cls.base = r.commit(
            "init",
            {
                "src/billing/core.py": "import requests\n\ndef charge(x):\n    return requests.post('u', json=x)\n"
                + "\n".join("def f%d(a):\n    return a\n" % i for i in range(60)),
                "src/billing/models.py": "class Invoice:\n    pass\n" * 40,
                "src/users/api.py": "from src.billing.core import charge\n\ndef sign_up(u):\n    return charge(u)\n"
                * 30,
                "tests/test_core.py": "from src.billing.core import charge\n\ndef test_charge():\n    assert charge\n",
            },
            when=days_ago(4),
        )
        cls.head = r.commit(
            "feat: users",
            {
                "src/users/api.py": "def sign_up(u):\n    return u\n",
                "docs/x.md": "doc\n",
                "infra/main.tf": "resource {}\n",
            },
            when=days_ago(2),
        )
        cls.out = str(tmp_path_factory.mktemp("out"))

    def test_rate_and_report(self):
        r = run(self.repo.path, "rate", "--depth", "2", "--out", self.out)
        assert r.returncode == 0, r.stderr
        data = json.loads(read(os.path.join(self.out, "data.json")))
        assert sorted(m["name"] for m in data["modules"]) == ["src/billing", "src/users"]
        assert "Policy: clean-eval default" in data["notes"]
        report = run(self.repo.path, "report", os.path.join(self.out, "data.json"))
        assert "| `src/" in report.stdout

    def test_the_readme_section_is_the_only_publishing_output(self):
        usage = run(self.repo.path, "--help").stdout
        assert "{rate,readme,report,assess,history,prepare,finalize,post,redact,guard}" in usage
        for gone in ("render", "diagram"):
            assert run(self.repo.path, gone, "x").returncode != 0, gone

    def test_rate_a_past_revision(self):
        out = os.path.join(self.out, "rev")
        r = run(self.repo.path, "rate", "--rev", self.base, "--out", out)
        assert r.returncode == 0, r.stderr
        assert json.loads(read(os.path.join(out, "data.json")))["ref"] == self.base
        bad = run(self.repo.path, "rate", "--rev", self.base, "--graph", "code-review-graph", "--out", out)
        assert bad.returncode != 0
        assert "only --graph builtin" in bad.stderr

    def test_readme_is_written_once_then_left_alone(self, repo):
        repo.commit("init", {"app/a.py": "def a():\n    return 1\n", "README.md": "# App\n"}, when=days_ago(2))
        out = os.path.join(self.out, "readme")
        assert run(repo.path, "rate", "--out", out).returncode == 0
        status = os.path.join(out, "status.json")
        first = run(repo.path, "readme", os.path.join(out, "data.json"), "--status-json", status)
        assert first.returncode == 0, first.stderr
        assert json.loads(read(status))["readme_changed"]
        text = read(os.path.join(repo.path, "README.md"))
        assert text.startswith("# App\n\n## Risk Heat Map\n\n<!-- risk-heat-map:start -->")
        assert "](docs/risk-heatmap.svg)" in text
        assert os.path.exists(os.path.join(repo.path, "docs", "risk-heatmap.svg"))
        again = run(repo.path, "readme", os.path.join(out, "data.json"), "--status-json", status)
        assert "unchanged" in again.stdout
        assert not json.loads(read(status))["readme_changed"]

    def test_assess_a_change_from_its_base(self):
        r = run(self.repo.path, "assess", "--base", self.base, "--head", self.head)
        assert r.returncode == 0, r.stderr
        risk = json.loads(r.stdout)
        assert risk["files"]["src/users/api.py"] == "src/users"
        assert risk["ignored"] == ["docs/x.md"]
        mods = {m["module"]: m for m in risk["modules"]}
        assert not mods["infra"]["rated"]
        assert mods["infra"]["band"] == "Moderate"
        assert risk["max_band"] in ("Moderate", "High", "Critical")

    def test_invalid_policy_is_reported_not_raised(self, repo):
        repo.commit(
            "init", {"a/b.py": "x = 1\n", ".clean-eval/risk-model.json": '{"window_days": 30}'}, when=days_ago(1)
        )
        r = run(repo.path, "rate", "--out", os.path.join(self.out, "bad"))
        assert r.returncode != 0
        assert "v1 model" in r.stderr
        assert "Traceback" not in r.stderr


class TestHistoryCommand:
    """The eval skill runs `uv run ... python -I <checkout>/clean_eval history ...` from the repository it evaluates."""

    @pytest.fixture(autouse=True)
    def folder(self, tmp_path):
        self.dir = str(tmp_path)

    def run_ce(self, *args):
        return subprocess.run(
            [sys.executable, "-I", CE, "history", *args], cwd=self.dir, capture_output=True, text=True
        )

    def test_replay_and_sensitivity_from_any_folder(self):
        r = Repo(self.dir)
        r.commit("init", {"src/a/x.py": "1\n"}, when=D("2026-01-01"))
        r.commit("feat: a (#1)", {"src/a/x.py": "2\n"}, when=D("2026-02-01"))
        r.commit("fix: a (#2)", {"src/a/x.py": "3\n"}, when=D("2026-02-02"))
        out = os.path.join(self.dir, "h.json")
        res = self.run_ce("--since", "2026-01-15", "--until", "2026-02-28", "--out", out)
        assert res.returncode == 0, res.stderr
        assert "2 PRs, 1 with escaped defects" in res.stderr
        write_json(os.path.join(self.dir, "candidate.json"), {"business_weights": {"dependents": 0.4}})
        replayed = self.run_ce("--from-history", out, "--replay", os.path.join(self.dir, "candidate.json"))
        assert set(json.loads(replayed.stdout)) == {"auc_max_score", "by_band", "band_mix"}
        rows = json.loads(self.run_ce("--from-history", out, "--sensitivity").stdout)
        assert rows[0]["change"] == "baseline"

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["--sensitivity"], "need --from-history"),
            ([], "--since is required"),
            (["--since", "last week"], "YYYY-MM-DD"),
        ],
        ids=["sensitivity without a history", "no period", "not a date"],
    )
    def test_usage_errors_are_reported_not_raised(self, args, message):
        Repo(self.dir)
        res = self.run_ce(*args)
        assert res.returncode != 0
        assert message in res.stderr
        assert "Traceback" not in res.stderr
