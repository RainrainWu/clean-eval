"""GitHub Actions, the runner a CI run is on: the step's outputs and the job summary, each a file the runner names in
the environment ($GITHUB_OUTPUT and $GITHUB_STEP_SUMMARY, which the run's Settings read). It is the use cases' Runner
interface, and it writes through the FileSystem gateway."""

from typing import Optional

from clean_eval.domain.review import StepOutputs
from clean_eval.use_cases.interfaces import (
    FileSystem,
    Runner,
)


class GitHubActionsGateway(Runner):
    def __init__(
        self,
        fs: FileSystem,
        outputs_file: str,
        summary_file: Optional[str] = None,
    ) -> None:
        self.fs = fs  # appends to the runner's files
        self.outputs_file = outputs_file  # the step-outputs file: one `name=value` line per output
        self.summary_file = summary_file  # the job-summary file (Markdown); None for a step that has none

    def set_outputs(
        self,
        outputs: StepOutputs,
    ) -> None:
        # Each value is one line (StepOutputs validates it), so no value can start an output of its own.
        self.fs.append_text(
            self.outputs_file, "".join("%s=%s\n" % (name, value) for name, value in outputs.root.items())
        )

    def add_summary(
        self,
        title: str,
        report: str,
    ) -> None:
        if self.summary_file is not None:
            self.fs.append_text(self.summary_file, "## clean-eval · %s\n\n%s" % (title, report))
