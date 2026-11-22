"""The steps every CI run shares (use_cases/ci.py), with their dependencies mocked: the run's folders, the agent's run
and its transcript, and the job summary."""

import pytest

from clean_eval.domain.errors import HeatmapError, RunFailed
from clean_eval.domain.review import AgentRun, RunFolders
from clean_eval.use_cases.ci import TRANSCRIPT, report_run, run_agent, start_run

TOKEN = "ghs_" + "t" * 36  # a GitHub token in the environment, which no transcript may keep
FOLDERS = RunFolders(workspace="/repo", out="/out", context="/out/context", agent="/out/agent")
# What a runner holds before a run: the skills in the action's checkout, an earlier run's output folder, and the
# step-outputs file.
BEFORE_A_RUN = {
    "/action/skills/pr-review/SKILL.md": "# PR review\n",
    "/out/context/old.json": "{}",
    "/out/agent/result.json": "{}",
    "/out/report.md": "old",
    "/out/claude.log": "old",
    "/out/keep.txt": "not the run's",
    "/runner/outputs": "",
}


@pytest.mark.parametrize("files", [BEFORE_A_RUN], indirect=True)
class TestStartRun:
    """A run starts in fresh folders, with the skills in its context and its folders named in the step outputs; it
    refuses a folder that is no git checkout, or a shallow one, before it touches anything."""

    def test_the_folders_are_made_afresh(
        self,
        git,
        fs,
        memory_fs,
    ):
        assert start_run(git, fs, "/repo", "/out", "/action/skills", "/runner/outputs") == FOLDERS
        assert memory_fs.files_under("/out/context") == ["skills/pr-review/SKILL.md"]
        assert memory_fs.files_under("/out/agent") == []
        assert not memory_fs.exists("/out/report.md")
        assert not memory_fs.exists("/out/" + TRANSCRIPT)
        assert memory_fs.read_text("/out/keep.txt") == "not the run's"
        assert memory_fs.read_text("/runner/outputs") == "output-dir=/out\nresult=/out/result.json\n"

    @pytest.mark.parametrize("git", [{"toplevel.side_effect": HeatmapError("not a git repository")}], indirect=True)
    def test_a_folder_that_is_no_checkout_is_refused(
        self,
        git,
        fs,
        memory_fs,
    ):
        with pytest.raises(RunFailed, match="/repo is not a git repository"):
            start_run(git, fs, "/repo", "/out", "/action/skills", "/runner/outputs")
        assert memory_fs.read_text("/out/report.md") == "old"

    @pytest.mark.parametrize("git", [{"is_shallow.return_value": True}], indirect=True)
    def test_a_shallow_clone_is_refused(
        self,
        git,
        fs,
        memory_fs,
    ):
        with pytest.raises(RunFailed, match="shallow clone"):
            start_run(git, fs, "/repo", "/out", "/action/skills", "/runner/outputs")
        assert memory_fs.read_text("/out/report.md") == "old"


@pytest.mark.parametrize("files", [{"/out/agent/.keep": ""}], indirect=True)
class TestRunAgent:
    """The agent runs on the prompt with the folders it may write, and its transcript is kept redacted."""

    @pytest.mark.parametrize("environ", [{"GH_TOKEN": TOKEN}], indirect=True)
    @pytest.mark.parametrize(
        "agent", [{"run.return_value": AgentRun(status=0, transcript="read %s\n" % TOKEN)}], indirect=True
    )
    def test_the_transcript_is_kept_redacted(
        self,
        agent,
        fs,
        memory_fs,
        env,
        run_log,
    ):
        run_agent(agent, fs, env, run_log, FOLDERS, "Review it.", ["/repo/.clean-eval"])
        agent.run.assert_called_once_with("Review it.", FOLDERS, ["/repo/.clean-eval"])
        assert memory_fs.read_text("/out/" + TRANSCRIPT) == "read [REDACTED]\n"
        run_log.step.assert_called_once_with("Claude: Review it.…")
        run_log.show.assert_called_once_with("read [REDACTED]\n")
        run_log.warning.assert_not_called()

    @pytest.mark.parametrize("agent", [{"run.return_value": AgentRun(status=3, transcript="boom")}], indirect=True)
    def test_a_failed_run_is_a_warning(
        self,
        agent,
        fs,
        env,
        run_log,
    ):
        run_agent(agent, fs, env, run_log, FOLDERS, "Review it.")
        agent.run.assert_called_once_with("Review it.", FOLDERS, [])
        run_log.warning.assert_called_once_with("claude exited with status 3 (see claude.log)")


@pytest.mark.parametrize("files", [{"/out/report.md": "## Review\n", "/runner/summary.md": "earlier\n"}], indirect=True)
class TestReportRun:
    """The run's report goes into the job summary, under the run's title."""

    def test_the_report_is_added_to_the_job_summary(
        self,
        fs,
        memory_fs,
    ):
        report_run(fs, "/runner/summary.md", "PR Review #7", FOLDERS)
        assert memory_fs.read_text("/runner/summary.md") == "earlier\n## clean-eval · PR Review #7\n\n## Review\n"
