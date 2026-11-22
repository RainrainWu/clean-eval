"""The command line, built on click: every utility of clean-eval is one of its commands. Each command parses its
options, hands a use case the gateways and presenters this module builds (it is the composition root), runs it and
prints the outcome.

    uv run python -I <clean-eval checkout>/clean_eval <command> ...

`clean_eval --help` lists the commands, and `clean_eval <command> --help` their options."""

import json
import shlex
import sys
from collections.abc import Callable
from typing import (
    IO,
    Any,
    Optional,
    Union,
)

import click
from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.adaptor.presenters import (
    MarkdownReportPresenter,
    SvgDiagramPresenter,
)
from clean_eval.domain.errors import (
    HeatmapError,
    InvalidOutput,
    InvalidRequest,
)
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import (
    Heatmap,
    Judgement,
    RatingCache,
    ReadmeAction,
)
from clean_eval.domain.retro import (
    Period,
    Replay,
    RetroHistory,
    SensitivityRow,
)
from clean_eval.domain.review import (
    RunFolders,
    Secrets,
    Skill,
)
from clean_eval.external import agent_guard
from clean_eval.external.claude_code import ClaudeCodeGateway
from clean_eval.external.cobertura import CoberturaGateway
from clean_eval.external.environment import (
    ClockGateway,
    EnvironmentGateway,
)
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.external.github import GitHubGateway
from clean_eval.external.github_actions import GitHubActionsGateway
from clean_eval.settings import (
    DIAGRAM_PATH,
    Settings,
)
from clean_eval.use_cases.ci import start_run
from clean_eval.use_cases.heatmap import (
    finalize_heatmap_report,
    rate_revision,
    rate_working_tree,
    run_heatmap_action,
    summarize_heatmap,
    update_readme,
)
from clean_eval.use_cases.interfaces import (
    Agent,
    CodeHost,
    CoverageReport,
    DiagramPresenter,
    Git,
    ReportPresenter,
    RunLog,
    Runner,
)
from clean_eval.use_cases.retro import (
    collect_history,
    finalize_retro,
    prepare_retro,
    run_retro_action,
)
from clean_eval.use_cases.review import (
    assess_change,
    finalize_pr_review,
    post_review,
    prepare_review,
    run_review_action,
)
from clean_eval.use_cases.rules.retro import (
    replay,
    rules_warning,
    sensitivity,
)
from clean_eval.use_cases.rules.review import redact as redact_text
from clean_eval.use_cases.store import (
    load_heatmap,
    load_judgement,
    load_policy,
    read_json,
    save_heatmap,
)

# The stateless gateways, shared; git is per repository, gh per use.
FS, ENV, CLOCK = FileSystemGateway(), EnvironmentGateway(), ClockGateway()
CHECKOUT = FS.parent(FS.parent(FS.parent(FS.real_path(__file__))))  # the clean-eval checkout: package and skills
DEFAULT_RULES = FS.join(CHECKOUT, ".claude", "skills", "pr-review", "review-rules.md")
SKILLS_DIR = FS.join(CHECKOUT, ".claude", "skills")  # the skills the CI agent follows
PACKAGE_DIR = FS.join(CHECKOUT, "clean_eval")
HELP = """Rate how risky each sub-module of a repository is to change, put the heat map into its README, and assess and
review changes by that risk.

The commands marked CI are the ones the GitHub Action runs (`action` runs the others around its agent, which only
proposes): they
decide and act."""
ACTION_HELP = """CI: run SKILL (risk-heat-map, pr-review or retro) on a checkout, as the GitHub Action does. A run that fails
reports an ::error:: and exits with status 1.

Its inputs are environment variables, which the run reads once into its settings (clean_eval/settings.py): action.yml
sets the CE_* ones from the action's inputs, and the runner sets the rest. An empty variable counts as unset."""


# The gateways and presenters the commands hand the use cases.


def git(
    repo: str,
) -> Git:
    return GitGateway(
        repo,
    )


def github() -> CodeHost:
    return GitHubGateway()


def coverage_report() -> CoverageReport:
    return CoberturaGateway(
        FS,
    )


def diagram_presenter() -> DiagramPresenter:
    return SvgDiagramPresenter()


def report_presenter() -> ReportPresenter:
    return MarkdownReportPresenter()


def today() -> str:
    return CLOCK.today()


def secrets() -> Secrets:
    """What to redact: the secrets of this process's environment."""
    return ENV.secrets()


def default_rules() -> str:
    return FS.read_text(DEFAULT_RULES)


def runner_for(
    settings: Settings,
) -> Runner:
    """GitHub Actions, the runner of a CI run: its step outputs and job summary go to the files the settings name."""
    return GitHubActionsGateway(
        FS,
        settings.github.step_outputs,
        settings.github.step_summary,
    )


def agent_for(
    settings: Settings,
    folders: RunFolders,
) -> Agent:
    """Claude Code for a CI run, sandboxed: its PreToolUse hook is this checkout's guard, run by this Python, and a
    failing hook denies the call (`|| exit 2`); npm installs it from the run's output folder, away from the checkout;
    and it follows this checkout's skills."""
    guard = "%s -I %s guard || exit 2" % (shlex.quote(sys.executable), shlex.quote(PACKAGE_DIR))
    return ClaudeCodeGateway(
        FS,
        settings,
        ENV.variables(),
        guard,
        folders.out,
        SKILLS_DIR,
    )


def run_settings() -> Settings:
    """A CI run's settings, read from this process's environment; HeatmapError names each variable that doesn't fit."""
    try:
        return Settings()
    except ValidationError as error:
        raise HeatmapError.from_validation(error, "invalid settings")


def action_help() -> str:
    """The `action` command's help: ACTION_HELP, then each group of settings, with each setting's variable, what it is
    for, and its default."""
    groups: dict[str, list[str]] = {}
    for setting, field in Settings.fields().items():
        description, default = field.description or "", field.get_default()
        if default not in (None, "") and "(default:" not in description:
            description += " (default: %s)" % (str(default).lower() if isinstance(default, bool) else default)
        line = "  %-31s %s" % (Settings.variables(setting)[0], description)
        groups.setdefault(setting.split(".")[0], []).append(line)
    paragraphs = ["\b\n%s:\n%s" % (group, "\n".join(lines)) for group, lines in groups.items()]
    return "\n\n".join([ACTION_HELP, *paragraphs])


def repository_root(
    path: str,
) -> str:
    return GitGateway(
        path,
    ).toplevel()


def period(
    since: str,
    until: str,
    window_days: int = 30,
) -> Period:
    """The Period of PRs merged from `since` to `until` (YYYY-MM-DD, inclusive), with `window_days` of look-ahead."""
    try:
        return Period(
            since=since,
            until=until,
            window_days=window_days,
        )
    except ValidationError as e:
        raise InvalidRequest.from_validation(e, "since/until must be dates as YYYY-MM-DD")


def heatmap_and_judgement(
    data: str,
    judgement_path: Optional[str],
) -> tuple[Heatmap, Optional[Judgement]]:
    """The rated heat map in `data` (data.json), and the levels in `judgement_path` when there is one."""
    judgement = load_judgement(FS, judgement_path) if judgement_path else None
    return load_heatmap(FS, data), judgement


def rescored_history(
    root: str,
    policy: Policy,
    from_history: Optional[str],
    replay_overrides: Optional[str],
) -> Union[Replay, list[SensitivityRow]]:
    """The collected history in `from_history`, re-scored: replayed under the policy `replay_overrides` describes, else
    its one-knob sensitivity to `policy`."""
    if not from_history:
        raise InvalidRequest(
            "--replay/--sensitivity need --from-history",
        )
    try:
        collected = RetroHistory.model_validate(read_json(FS, from_history))
    except ValidationError as e:
        raise HeatmapError.from_validation(e, "%s is not a collected history" % from_history)
    if replay_overrides:
        return replay(collected, load_policy(FS, root, replay_overrides)[0])
    return sensitivity(collected, policy)


def print_or_write(
    result: Union[RetroHistory, Replay, list[SensitivityRow]],
    out: Optional[str],
) -> None:
    """Print `result` as JSON, or write it to `out` and say what a collected history holds."""
    text = json.dumps(to_jsonable_python(result), indent=2)
    if not out:
        print(text)
        return
    FS.write_text(out, text)
    if isinstance(result, RetroHistory):
        print(
            "wrote %s: %d PRs, %d with escaped defects" % (out, result.totals.prs, result.totals.defective_prs),
            file=sys.stderr,
        )


def run_risk_heat_map(
    settings: Settings,
    folders: RunFolders,
    agent: Agent,
    runner: Runner,
    log: RunLog,
) -> None:
    """The risk heat map's CI run in `folders`, with the gateways and presenters it takes."""
    run_heatmap_action(
        git(folders.workspace),
        FS,
        coverage_report(),
        CLOCK,
        ENV,
        agent,
        diagram_presenter(),
        report_presenter(),
        runner,
        log,
        folders,
        settings,
    )


def run_pr_review(
    settings: Settings,
    folders: RunFolders,
    agent: Agent,
    runner: Runner,
    log: RunLog,
) -> None:
    """The PR review's CI run in `folders`, with the gateways it takes."""
    run_review_action(
        github(),
        git(repository_root(folders.workspace)),
        FS,
        CLOCK,
        ENV,
        agent,
        runner,
        log,
        folders,
        settings,
        default_rules(),
    )


def run_retro(
    settings: Settings,
    folders: RunFolders,
    agent: Agent,
    runner: Runner,
    log: RunLog,
) -> None:
    """The retro's CI run in `folders`, with the gateways it takes."""
    run_retro_action(
        git(repository_root(folders.workspace)),
        FS,
        CLOCK,
        ENV,
        github(),
        agent,
        runner,
        log,
        folders,
        settings,
        default_rules(),
    )


def run_skill(
    skill: Skill,
    settings: Settings,
    folders: RunFolders,
    agent: Agent,
    runner: Runner,
    log: RunLog,
) -> None:
    """Run `skill`'s CI run in `folders`."""
    if skill == Skill.RISK_HEAT_MAP:
        run_risk_heat_map(settings, folders, agent, runner, log)
    elif skill == Skill.PR_REVIEW:
        run_pr_review(settings, folders, agent, runner, log)
    else:
        run_retro(settings, folders, agent, runner, log)


# The command line.


class ConsoleRunLog(RunLog):
    """A CI run's log, on this process's stderr in GitHub Actions' workflow commands: each step opens a collapsible
    group (`in_group` says whether one is open), and a warning is an annotation."""

    def __init__(
        self,
    ) -> None:
        self.in_group = False

    def step(
        self,
        title: str,
    ) -> None:
        if self.in_group:
            print("::endgroup::", file=sys.stderr)
        print("::group::%s" % title, file=sys.stderr)
        self.in_group = True

    def warning(
        self,
        message: str,
    ) -> None:
        print("::warning::%s" % message, file=sys.stderr)

    def show(
        self,
        text: str,
    ) -> None:
        print(text, file=sys.stderr)


class UserError(click.ClickException):
    """A failure the user can act on (a HeatmapError): it reads `clean_eval: <message>`, with exit status 1."""

    def show(
        self,
        file: Optional[IO[Any]] = None,
    ) -> None:
        click.echo("clean_eval: %s" % self.format_message(), file=file, err=True)


class CommandLine(click.Group):
    """clean_eval's commands. A HeatmapError one of them raises is reported as a UserError, never as a traceback."""

    def invoke(
        self,
        ctx: click.Context,
    ) -> object:
        try:
            return super().invoke(ctx)
        except HeatmapError as error:
            raise UserError(
                str(error),
            ) from error


@click.group(cls=CommandLine, help=HELP, context_settings={"help_option_names": ["-h", "--help"]})
def clean_eval() -> None:
    """The command group; its commands follow."""


def repository_options(
    command: Callable[..., None],
) -> Callable[..., None]:
    """The options of a command that reads a repository: --repo, and --model for its risk-model overrides."""
    command = click.option(
        "--model", help="risk-model overrides (default: <repo>/.clean-eval/risk-model.json when present)"
    )(command)
    return click.option("--repo", default=".", help="repository root (default: current folder)")(command)


def heatmap_options(
    command: Callable[..., None],
) -> Callable[..., None]:
    """The arguments of a command that reads a rated heat map: DATA (data.json from rate), and --judgement."""
    command = click.option("--judgement", help="optional judgement.json with the aspect levels")(command)
    return click.argument("data")(command)


# The commands people run.


@clean_eval.command(
    short_help="Rate every module on the four aspects.",
    help="Rate every module on the four aspects, into <out>/data.json.",
)
@repository_options
@click.option("--rev", help="rate this revision, read from git objects (default: the working tree)")
@click.option("--depth", type=int, help="levels of modules: 1 is the top-level folders (default from the policy: 3)")
@click.option("--max-modules", type=int, help="at most this many modules (default from the policy: 60)")
@click.option("--out", required=True, help="folder for data.json")
def rate(
    repo: str,
    model: Optional[str],
    rev: Optional[str],
    depth: Optional[int],
    max_modules: Optional[int],
    out: str,
) -> None:
    policy, source = load_policy(FS, repo, model)
    if rev:
        heatmap = rate_revision(git(repo), CLOCK, rev, policy, RatingCache(), depth, max_modules)
    else:
        heatmap = rate_working_tree(
            git(repo), FS, coverage_report(), CLOCK, policy, depth=depth, max_modules=max_modules
        )
    heatmap.notes.append("Policy: %s" % source)
    save_heatmap(FS, heatmap, FS.join(out, "data.json"))
    print(
        "%s @ %s (%s): %d modules, %d source lines -> %s"
        % (heatmap.repo, heatmap.ref, heatmap.sha, len(heatmap.modules), heatmap.loc_total, FS.join(out, "data.json"))
    )
    for module in sorted(heatmap.modules, key=lambda rated: -rated.scores.overall)[:10]:
        scores = module.scores
        print(
            "%-45s loc %-7d overall %-3d biz %-3d effects %-3d entropy %-3d unverified %-3s"
            % (
                module.name,
                module.loc,
                scores.overall,
                scores.biz,
                scores.effects,
                scores.entropy,
                "N/A" if scores.unverified is None else scores.unverified,
            )
        )
    for note in heatmap.notes:
        print("note:", note)


@clean_eval.command(
    short_help="Put the heat map into the README.",
    help="Put the heat map into the README section and its diagram file; nothing changes when the ratings are the "
    "same. DATA is the data.json from rate.",
)
@repository_options
@heatmap_options
@click.option("--readme", "readme_path", default="README.md", help="README path, relative to --repo")
@click.option(
    "--svg", "svg_path", default=DIAGRAM_PATH, help="diagram path, relative to --repo (default %s)" % DIAGRAM_PATH
)
@click.option("--status-json", help="write {readme_changed, action, fingerprint, modules} here")
def readme(
    repo: str,
    model: Optional[str],
    data: str,
    judgement: Optional[str],
    readme_path: str,
    svg_path: str,
    status_json: Optional[str],
) -> None:
    heatmap, levels = heatmap_and_judgement(data, judgement)
    root = FS.absolute(repo)
    result = update_readme(
        FS,
        diagram_presenter(),
        FS.join(root, readme_path),
        FS.join(root, svg_path),
        heatmap,
        levels,
        load_policy(FS, repo, model)[0],
    )
    if status_json:
        FS.write_text(
            status_json,
            json.dumps(
                {
                    "readme_changed": result.changed,
                    "action": result.action,
                    "fingerprint": result.fingerprint,
                    "modules": len(result.heatmap.modules),
                }
            ),
        )
    if result.changed:
        verb = "added" if result.action == ReadmeAction.ADD else "updated"
        print("%s the heat map in %s and %s (%d modules)" % (verb, readme_path, svg_path, len(result.heatmap.modules)))
    else:
        print("unchanged: %s already shows these ratings (fingerprint %s)" % (readme_path, result.fingerprint))


@clean_eval.command(
    short_help="Summarize a heat map in Markdown.",
    help="A short Markdown summary: the top modules and what drives them. DATA is the data.json from rate.",
)
@repository_options
@heatmap_options
@click.option("--out", help="write here instead of stdout")
@click.option("--json-out", help="also write the finalized heatmap (levels applied) as JSON")
def report(
    repo: str,
    model: Optional[str],
    data: str,
    judgement: Optional[str],
    out: Optional[str],
    json_out: Optional[str],
) -> None:
    heatmap, levels = heatmap_and_judgement(data, judgement)
    final, text = summarize_heatmap(report_presenter(), heatmap, levels, load_policy(FS, repo, model)[0])
    if json_out:
        save_heatmap(FS, final, json_out)
    if out:
        FS.write_text(out, text)
    else:
        sys.stdout.write(text)


@clean_eval.command(
    short_help="The risk a change carries.",
    help="The risk a change (base..head) carries, from the heat map of its base.",
)
@repository_options
@click.option("--base", required=True)
@click.option("--head", required=True)
@click.option("--out", help="write the JSON here instead of stdout")
def assess(
    repo: str,
    model: Optional[str],
    base: str,
    head: str,
    out: Optional[str],
) -> None:
    policy, source = load_policy(FS, repo, model)
    heatmap, risk = assess_change(git(repo), CLOCK, base, head, policy)
    result = risk.model_dump(mode="json")
    result.update(base=base, head=head, model_source=source, model_version=policy.version, notes=heatmap.notes)
    text = json.dumps(result, indent=1)
    if out:
        FS.write_text(out, text)
        print(
            "%s..%s: %s (max score %d) across %d modules -> %s"
            % (base[:8], head[:8], risk.max_band, risk.max_score, len(risk.modules), out)
        )
    else:
        print(text)


@clean_eval.command(
    short_help="Merged PRs of a period, with escaped defects.",
    help="Merged PRs of a period, each rated at its base, with escaped defects (for the retro). --replay and "
    "--sensitivity re-score a collected history under candidate policies.",
)
@repository_options
@click.option("--since", help="YYYY-MM-DD (required unless --from-history)")
@click.option("--until", default=today, show_default="today", help="YYYY-MM-DD")
@click.option("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
@click.option("--defect-window-days", type=int, default=30, show_default=True)
@click.option("--with-gh", is_flag=True, help="fetch review comments with gh (rule metrics)")
@click.option(
    "--reviewer-login",
    "reviewer_logins",
    multiple=True,
    help="login whose clean-eval markers count (repeatable; default: any [bot] login)",
)
@click.option("--from-history", help="reuse a collected history JSON instead of walking git")
@click.option(
    "--replay",
    "replay_overrides",
    metavar="OVERRIDES_JSON",
    help="calibration of candidate risk-model overrides on --from-history",
)
@click.option(
    "--sensitivity", "with_sensitivity", is_flag=True, help="one-knob replays of the policy on --from-history"
)
@click.option("--out", help="write the JSON here instead of stdout")
def history(
    repo: str,
    model: Optional[str],
    since: Optional[str],
    until: str,
    branch: Optional[str],
    defect_window_days: int,
    with_gh: bool,
    reviewer_logins: tuple[str, ...],
    from_history: Optional[str],
    replay_overrides: Optional[str],
    with_sensitivity: bool,
    out: Optional[str],
) -> None:
    root = repository_root(repo)
    policy, source = load_policy(FS, root, model)
    if replay_overrides or with_sensitivity:
        result = rescored_history(root, policy, from_history, replay_overrides)
    else:
        if not since:
            raise InvalidRequest(
                "--since is required (or --from-history with --replay/--sensitivity)",
            )
        result = collect_history(
            git(root),
            github(),
            CLOCK,
            policy,
            period(since, until, defect_window_days),
            with_reviews=with_gh,
            branch=branch,
            model_source=source,
            reviewer_logins=list(reviewer_logins) or None,
        )
        warning = rules_warning(result.rules) if result.rules else None
        if warning:
            print("warning: " + warning, file=sys.stderr)
    print_or_write(result, out)


# The commands the GitHub Action runs around its agent.


@clean_eval.group(
    short_help="CI: precompute the agent's context.",
    help="CI: precompute the agent's context, so it runs with no shell, no network and no token.",
)
def prepare() -> None:
    """The group of the two kinds of context."""


@prepare.command("pr-review", help="The context of a PR review: its diff, history, risk, and its base's config.")
@click.option("--repo", default=".", help="repository root (default: current folder)")
@click.option("--ctx", "context_dir", required=True, help="folder for the context")
@click.option("--pr", type=int, required=True)
def prepare_pr_review(
    repo: str,
    context_dir: str,
    pr: int,
) -> None:
    root = repository_root(repo)
    FS.make_dirs(context_dir)
    context = prepare_review(github(), git(root), FS, CLOCK, context_dir, pr, default_rules())
    print(context.model_dump_json())


@prepare.command(
    "retro", help="The context of a retro: the period's history, its escaped defects and the tuning files."
)
@click.option("--repo", default=".", help="repository root (default: current folder)")
@click.option("--ctx", "context_dir", required=True, help="folder for the context")
@click.option("--since", required=True, help="YYYY-MM-DD")
@click.option("--until", required=True, help="YYYY-MM-DD")
@click.option("--with-gh", is_flag=True, help="fetch review comments with gh")
@click.option("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
def prepare_retro_context(
    repo: str,
    context_dir: str,
    since: str,
    until: str,
    with_gh: bool,
    branch: Optional[str],
) -> None:
    root = repository_root(repo)
    FS.make_dirs(context_dir)
    policy, source = load_policy(FS, root)
    context = prepare_retro(
        git(root),
        FS,
        CLOCK,
        github(),
        context_dir,
        root,
        policy,
        source,
        period(since, until),
        default_rules(),
        with_reviews=with_gh,
        branch=branch,
    )
    print(context.model_dump_json())


@clean_eval.command(
    short_help="CI: validate the agent's output.",
    help="CI: validate the agent's output and write the result CI trusts, redacted, and the step outputs.",
)
@click.option(
    "--skill",
    type=click.Choice(
        [skill.value for skill in Skill],
    ),
    required=True,
)
@click.option("--ctx", "context_dir", required=True)
@click.option("--agent", required=True)
@click.option("--out", required=True)
@click.option("--workspace", default=".")
@click.option("--apply", "apply_edits", is_flag=True, help="retro: keep the edits of .clean-eval/ that pass validation")
@click.option("--gh-output", required=True, help="the step-outputs file ($GITHUB_OUTPUT)")
@click.pass_context
def finalize(
    context: click.Context,
    skill: str,
    context_dir: str,
    agent: str,
    out: str,
    workspace: str,
    apply_edits: bool,
    gh_output: str,
) -> None:
    try:
        if skill == Skill.PR_REVIEW:
            result, outputs = finalize_pr_review(FS, ENV, context_dir, agent, out)
        elif skill == Skill.RETRO:
            result, outputs = finalize_retro(FS, ENV, context_dir, agent, out, apply_edits, workspace)
        else:
            result, outputs = finalize_heatmap_report(FS, ENV, out)
    except InvalidOutput as e:
        print("::error::clean-eval: invalid agent output: %s" % e, file=sys.stderr)
        context.exit(2)
    if result is not None and result.redactions:
        print(
            "::warning::clean-eval redacted %d secret-looking value(s) from agent output" % result.redactions,
            file=sys.stderr,
        )
    GitHubActionsGateway(
        FS,
        gh_output,
    ).set_outputs(outputs)
    print(outputs.model_dump_json())


@clean_eval.command(
    short_help="CI: post the validated review.",
    help="CI: post the validated review on the PR (exit status 1 when no attempt went through).",
)
@click.option("--pr", type=int, required=True)
@click.option("--ctx", "context_dir", help="context folder with diff/index.json, to attach only comments on diff lines")
@click.option("--out", required=True)
@click.option("--head", required=True)
@click.pass_context
def post(
    context: click.Context,
    pr: int,
    context_dir: Optional[str],
    out: str,
    head: str,
) -> None:
    outcome = post_review(github(), FS, ENV, out, pr, head, context_dir)
    for level, message in outcome.log:
        if level == "warning":
            print("::warning::" + message, file=sys.stderr)
        else:
            print(message)
    if not outcome.posted:
        context.exit(1)


@clean_eval.command(
    short_help="CI: scrub secrets from a file.",
    help="CI: scrub secrets from the file at PATH, in place (e.g. the agent transcript).",
)
@click.argument("path")
def redact(
    path: str,
) -> None:
    if FS.exists(path):
        text, count = redact_text(FS.read_text(path), secrets())
        if count:
            FS.write_text(path, text)
            print(
                "::warning::clean-eval redacted %d secret-looking value(s) from %s" % (count, FS.name(path)),
                file=sys.stderr,
            )


@clean_eval.command(
    short_help="CI: the agent's PreToolUse hook.",
    help="CI: the agent's PreToolUse hook: the tool call on stdin; exit status 0 allows it, 2 denies it.",
)
@click.pass_context
def guard(
    context: click.Context,
) -> None:
    context.exit(agent_guard.hook())


@clean_eval.command(short_help="CI: run a skill as the GitHub Action does.", help=action_help())
@click.argument(
    "skill",
    type=click.Choice(
        [skill.value for skill in Skill],
    ),
)
@click.pass_context
def action(
    context: click.Context,
    skill: str,
) -> None:
    log = ConsoleRunLog()
    failure = None
    try:
        settings = run_settings()
        runner = runner_for(settings)
        folders = start_run(git(settings.run.workspace), FS, runner, settings)
        run_skill(
            Skill(
                skill,
            ),
            settings,
            folders,
            agent_for(settings, folders),
            runner,
            log,
        )
    except HeatmapError as error:
        failure = error
    if log.in_group:
        print("::endgroup::", file=sys.stderr)
    if failure is not None:
        print("::error::clean-eval: %s" % failure, file=sys.stderr)
        context.exit(1)


def main(
    argv: Optional[list[str]] = None,
) -> None:
    """Run the command line on `argv` (default: this process's arguments), and exit with the command's status."""
    clean_eval.main(args=argv, prog_name="clean_eval")
