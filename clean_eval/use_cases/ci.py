"""The CI run of a skill, the steps every skill's run shares (heatmap.py, review.py and evaluation.py run the skills):
its folders, made afresh with the skills the agent reads; the sandboxed agent's run and its redacted transcript; and the
run's report in the job summary. Through the Git, FileSystem, Environment, Agent and RunLog interfaces."""

from collections.abc import Sequence

from clean_eval.domain.errors import HeatmapError, RunFailed
from clean_eval.domain.review import RunFolders, StepOutputs
from clean_eval.use_cases.interfaces import Agent, Environment, FileSystem, Git, RunLog
from clean_eval.use_cases.rules.review import redact, secret_values
from clean_eval.use_cases.store import clear_path, write_step_outputs

TRANSCRIPT = "claude.log"  # the agent's transcript, redacted, in the output folder
# What an earlier run leaves in the output folder, besides the context and the agent's folder.
LEFT_BEHIND = ("result.json", "report.md", TRANSCRIPT, "heatmap.md")


def start_run(
    git: Git,
    fs: FileSystem,
    workspace: str,
    out: str,
    skills: str,
    gh_output: str,
) -> RunFolders:
    """The folders of a run on `workspace` (a full git checkout), with its outputs in `out`, made afresh: an earlier
    run's context, agent folder and outputs removed, and the skills (`skills`, their folder in the clean-eval checkout)
    copied into the context, where the agent reads them. The step outputs name the output folder and the result.
    RunFailed when the workspace is not a git checkout, or is a shallow one."""
    try:
        git.toplevel()
    except HeatmapError:
        raise RunFailed("%s is not a git repository" % workspace)
    if git.is_shallow():
        raise RunFailed("shallow clone — risk scoring needs history. Use actions/checkout with 'fetch-depth: 0'.")
    fs.make_dirs(out)
    root = fs.real_path(out)
    folders = RunFolders(
        workspace=fs.real_path(workspace),
        out=root,
        context=fs.join(root, "context"),
        agent=fs.join(root, "agent"),
    )
    for path in [folders.context, folders.agent, *(fs.join(root, name) for name in LEFT_BEHIND)]:
        clear_path(fs, path)
    fs.make_dirs(folders.context)
    fs.make_dirs(folders.agent)
    fs.copy_tree(skills, fs.join(folders.context, "skills"))
    write_step_outputs(fs, gh_output, StepOutputs({"output-dir": root, "result": fs.join(root, "result.json")}))
    return folders


def run_agent(
    agent: Agent,
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    prompt: str,
    writable: Sequence[str] = (),
) -> None:
    """Run the agent on `prompt` (it may also write under `writable`), and keep its transcript in the output folder,
    redacted of the environment's secrets. The log shows the transcript, and warns when the agent failed."""
    log.step("Claude: %s…" % prompt[:160])
    run = agent.run(prompt, folders, list(writable))
    transcript, _ = redact(run.transcript, secret_values(env.variables()))
    fs.write_text(fs.join(folders.out, TRANSCRIPT), transcript)
    log.show(transcript)
    if run.status:
        log.warning("claude exited with status %d (see %s)" % (run.status, TRANSCRIPT))


def report_run(
    fs: FileSystem,
    summary: str,
    title: str,
    folders: RunFolders,
) -> None:
    """Add the run's report (report.md in the output folder) to the job summary `summary`, under `title`."""
    fs.append_text(summary, "## clean-eval · %s\n\n%s" % (title, fs.read_text(fs.join(folders.out, "report.md"))))
