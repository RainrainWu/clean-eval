"""The steps every CI run shares (use_cases/ci.py), with their dependencies mocked: the run's folders, the agent's run
and its transcript, and what the run reports to its runner."""

import pytest
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from clean_eval.domain.errors import (
    HeatmapError,
    RunFailed,
)
from clean_eval.domain.review import (
    AgentRun,
    StepOutputs,
)
from clean_eval.use_cases.ci import (
    TRANSCRIPT,
    report_run,
    run_agent,
    start_run,
)


@pytest.mark.parametrize("environ", [lf("run_environment")], indirect=True)
@pytest.mark.parametrize("files", [lf("files_before_a_run")], indirect=True)
class TestStartRun:
    """A run starts in fresh folders, named in the step outputs; it refuses a folder that is no git checkout, or a
    shallow one, before it touches anything."""

    def test_the_folders_are_made_afresh(
        self,
        git,
        fs,
        memory_fs,
        runner,
        settings,
        run_folders,
    ):
        assert start_run(git, fs, runner, settings) == run_folders
        assert memory_fs.files_under("/out/context") == []
        assert memory_fs.files_under("/out/agent") == []
        assert not memory_fs.exists("/out/report.md")
        assert not memory_fs.exists("/out/" + TRANSCRIPT)
        assert memory_fs.read_text("/out/keep.txt") == "not the run's"
        runner.set_outputs.assert_called_once_with(
            StepOutputs(
                {"output-dir": "/out", "result": "/out/result.json"},
            )
        )

    @pytest.mark.parametrize(
        "git",
        [
            {
                "toplevel.side_effect": HeatmapError(
                    "not a git repository",
                )
            }
        ],
        indirect=True,
    )
    def test_a_folder_that_is_no_checkout_is_refused(
        self,
        git,
        fs,
        memory_fs,
        runner,
        settings,
    ):
        with pytest.raises(RunFailed, match="/repo is not a git repository"):
            start_run(git, fs, runner, settings)
        assert memory_fs.read_text("/out/report.md") == "old"
        runner.set_outputs.assert_not_called()

    @pytest.mark.parametrize("git", [{"is_shallow.return_value": True}], indirect=True)
    def test_a_shallow_clone_is_refused(
        self,
        git,
        fs,
        memory_fs,
        runner,
        settings,
    ):
        with pytest.raises(RunFailed, match="shallow clone"):
            start_run(git, fs, runner, settings)
        assert memory_fs.read_text("/out/report.md") == "old"
        runner.set_outputs.assert_not_called()


@pytest.mark.parametrize("files", [lf("empty_agent_folder")], indirect=True)
class TestRunAgent:
    """The agent runs on the task with the folders it may write, and its transcript is kept redacted."""

    @pytest.mark.parametrize("environ", [{"GH_TOKEN": lf("gh_token")}], indirect=True)
    @pytest.mark.parametrize(
        "agent",
        [
            lfc(
                lambda token: {
                    "run.return_value": AgentRun(
                        status=0,
                        transcript="read %s\n" % token,
                    )
                },
                lf("gh_token"),
            )
        ],
        indirect=True,
    )
    def test_the_transcript_is_kept_redacted(
        self,
        agent,
        fs,
        memory_fs,
        env,
        run_log,
        run_folders,
        agent_task,
    ):
        run_agent(agent, fs, env, run_log, run_folders, agent_task, ["/repo/.clean-eval"])
        agent.run.assert_called_once_with(agent_task, run_folders, ["/repo/.clean-eval"])
        assert memory_fs.read_text("/out/" + TRANSCRIPT) == "read [REDACTED]\n"
        run_log.step.assert_called_once_with("Run the agent: Review it.")
        run_log.show.assert_called_once_with("read [REDACTED]\n")
        run_log.warning.assert_not_called()

    @pytest.mark.parametrize(
        "agent",
        [
            {
                "run.return_value": AgentRun(
                    status=3,
                    transcript="boom",
                )
            }
        ],
        indirect=True,
    )
    def test_a_failed_run_is_a_warning(
        self,
        agent,
        fs,
        env,
        run_log,
        run_folders,
        agent_task,
    ):
        run_agent(agent, fs, env, run_log, run_folders, agent_task)
        agent.run.assert_called_once_with(agent_task, run_folders, [])
        run_log.warning.assert_called_once_with("the agent exited with status 3 (see claude.log)")


@pytest.mark.parametrize("files", [{"/out/report.md": "## Review\n"}], indirect=True)
class TestReportRun:
    """The run reports its step outputs to the runner, and its report goes into the job summary under the run's
    title."""

    def test_the_outputs_and_the_report_go_to_the_runner(
        self,
        fs,
        runner,
        run_folders,
    ):
        outputs = StepOutputs(
            {"verdict": "approve"},
        )
        report_run(fs, runner, run_folders, "PR Review #7", outputs)
        runner.set_outputs.assert_called_once_with(outputs)
        runner.add_summary.assert_called_once_with("PR Review #7", "## Review\n")
