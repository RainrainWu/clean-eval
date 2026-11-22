"""The steps every skill's CI run shares (heatmap.py, review.py and evaluation.py hold the runs): its folders, made
afresh; the agent's run on its task, with its transcript redacted; and the run's report to its runner. Through the Git,
FileSystem, Environment, Agent, Runner and RunLog interfaces, with the run's Settings."""

from collections.abc import Sequence

from clean_eval.domain.errors import (
    HeatmapError,
    RunFailed,
)
from clean_eval.domain.review import (
    AgentTask,
    RunFolders,
    StepOutputs,
)
from clean_eval.settings import Settings
from clean_eval.use_cases.interfaces import (
    Agent,
    Environment,
    FileSystem,
    Git,
    RunLog,
    Runner,
)
from clean_eval.use_cases.rules.review import (
    redact,
)
from clean_eval.use_cases.store import clear_path

TRANSCRIPT = "claude.log"  # the agent's transcript, redacted, in the output folder (the name the action documents)
MAX_PATCH_CHARS = 200_000  # the longest patch a run puts in the agent's context: a longer one is cut
# What an earlier run leaves in the output folder, besides the context and the agent's folder.
LEFT_BEHIND = ("result.json", "report.md", TRANSCRIPT, "heatmap.md")


def start_run(
    git: Git,
    fs: FileSystem,
    runner: Runner,
    settings: Settings,
) -> RunFolders:
    """The folders of a run on the settings' workspace (a full git checkout), with its outputs in their output folder,
    made afresh: an earlier run's context, agent folder and outputs removed. The runner's step outputs name the output
    folder and the result. RunFailed when the workspace is not a git checkout, or is a shallow one."""
    try:
        git.toplevel()
    except HeatmapError:
        raise RunFailed(
            "%s is not a git repository" % settings.run.workspace,
        )
    if git.is_shallow():
        raise RunFailed(
            "shallow clone — risk scoring needs the full history: fetch every commit",
        )
    fs.make_dirs(settings.run.out)
    root = fs.real_path(settings.run.out)
    folders = RunFolders(
        workspace=fs.real_path(settings.run.workspace),
        out=root,
        context=fs.join(root, "context"),
        agent=fs.join(root, "agent"),
    )
    for path in [folders.context, folders.agent, *(fs.join(root, name) for name in LEFT_BEHIND)]:
        clear_path(fs, path)
    fs.make_dirs(folders.context)
    fs.make_dirs(folders.agent)
    runner.set_outputs(
        StepOutputs(
            {"output-dir": root, "result": fs.join(root, "result.json")},
        ),
    )
    return folders


def run_agent(
    agent: Agent,
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    task: AgentTask,
    writable: Sequence[str] = (),
) -> None:
    """Run the agent on `task` (it may also write under `writable`), and keep its transcript in the output folder,
    redacted of the environment's secrets. The log shows the transcript, and warns when the agent failed."""
    log.step("Run the agent: %s" % task.goal)
    run = agent.run(task, folders, list(writable))
    transcript, _ = redact(run.transcript, env.secrets())
    fs.write_text(fs.join(folders.out, TRANSCRIPT), transcript)
    log.show(transcript)
    if run.status:
        log.warning("the agent exited with status %d (see %s)" % (run.status, TRANSCRIPT))


def report_run(
    fs: FileSystem,
    runner: Runner,
    folders: RunFolders,
    title: str,
    outputs: StepOutputs,
) -> None:
    """Report the run to its runner: its step `outputs`, and its report (report.md in the output folder) in the job
    summary, under `title`."""
    runner.set_outputs(outputs)
    runner.add_summary(title, fs.read_text(fs.join(folders.out, "report.md")))
