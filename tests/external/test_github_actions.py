"""The GitHub Actions gateway (external/github_actions.py), over real files (`fs`, the file-system gateway): the step
outputs as `name=value` lines and the job summary's sections, each appended to the file the runner names."""

import os

import pytest

from clean_eval.domain.review import StepOutputs
from clean_eval.external.github_actions import GitHubActionsGateway


class TestSetOutputs:
    """Step outputs are appended to the step-outputs file, one `name=value` line each."""

    @pytest.mark.parametrize("files", [{"outputs": "a=1\n"}], indirect=True)
    def test_the_outputs_are_appended_one_per_line(
        self,
        fs,
        root,
    ):
        outputs = os.path.join(root, "outputs")
        GitHubActionsGateway(
            fs,
            outputs,
        ).set_outputs(
            StepOutputs(
                {"verdict": "approve", "blockers": "0"},
            )
        )
        assert fs.read_text(outputs) == "a=1\nverdict=approve\nblockers=0\n"


class TestAddSummary:
    """A report goes into the job summary under its title, after what the summary holds; a step without a summary file
    writes none."""

    @pytest.mark.parametrize("files", [{"summary.md": "earlier\n"}], indirect=True)
    def test_the_report_is_appended_under_its_title(
        self,
        fs,
        root,
    ):
        summary = os.path.join(root, "summary.md")
        GitHubActionsGateway(
            fs,
            os.path.join(root, "outputs"),
            summary,
        ).add_summary("PR Review #7", "## Review\n")
        assert fs.read_text(summary) == "earlier\n## clean-eval · PR Review #7\n\n## Review\n"

    def test_a_step_without_a_summary_file_writes_none(
        self,
        fs,
        root,
    ):
        GitHubActionsGateway(
            fs,
            os.path.join(root, "outputs"),
        ).add_summary("PR Review #7", "## Review\n")
        assert os.listdir(root) == []
