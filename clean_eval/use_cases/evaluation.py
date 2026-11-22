"""Use cases of the eval: keep or restore the tuning files the CI agent edits, collect the merged-PR history of a
period, prepare the agent's context, and run the eval in CI."""

import datetime as dt
from collections.abc import (
    Callable,
    Collection,
)
from typing import (
    Optional,
    Union,
)

from pydantic import ValidationError

from clean_eval.domain.errors import (
    HeatmapError,
    InvalidOutput,
    NoPythonSource,
    RemoteError,
    RunFailed,
)
from clean_eval.domain.evaluation import (
    DefectPatch,
    EvalContext,
    EvalHistory,
    EvalResult,
    FetchFailure,
    HistoryPeriod,
    MergedPR,
    Period,
    Rejection,
    ReviewSignal,
    RuleStats,
    Totals,
)
from clean_eval.domain.policy import (
    REPO_DIR,
    Band,
    Policy,
)
from clean_eval.domain.rating import (
    Heatmap,
    ModuleRecord,
    RatingCache,
)
from clean_eval.domain.review import (
    RunFolders,
    StepOutputs,
)
from clean_eval.settings import (
    LOOKBACK_DAYS,
    Settings,
)
from clean_eval.use_cases.ci import (
    MAX_PATCH_CHARS,
    report_run,
    run_agent,
)
from clean_eval.use_cases.heatmap import rate_revision
from clean_eval.use_cases.interfaces import (
    Agent,
    Clock,
    CodeHost,
    Environment,
    FileSystem,
    Git,
    RunLog,
)
from clean_eval.use_cases.rules.evaluation import (
    MODEL_FILE,
    RULES_FILE,
    TUNING_FILES,
    attribute_defects,
    calibration,
    eval_recommendations,
    eval_result,
    in_period,
    judge_model_edit,
    judge_rules_edit,
    module_stats,
    pr_number,
    review_signal,
    rule_stats,
    sensitivity,
)
from clean_eval.use_cases.rules.review import (
    change_risk,
    secret_values,
    truncate,
)
from clean_eval.use_cases.store import (
    JUDGING_ERRORS,
    clear_path,
    invalid,
    load_policy,
    read_json,
    validated,
    write_json,
    write_run_output,
    write_step_outputs,
)

# The repository's tuning files (`.clean-eval/` in the working tree) and the snapshot of them taken before the CI agent
# ran, through the FileSystem interface: what an eval may change, and how anything it may not keep is put back.

SNAPSHOT = "clean-eval.orig"  # in the context folder: .clean-eval/ as it was before the agent ran


def text_or_none(
    fs: FileSystem,
    path: str,
) -> Optional[str]:
    return fs.read_text(path) if fs.is_file(path) else None


def snapshot_tuning(
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
) -> None:
    """Copy .clean-eval/ aside, as it is now."""
    live = fs.join(workspace, REPO_DIR)
    if fs.is_dir(live):
        fs.copy_tree(live, snapshot_dir)


def current_tuning(
    fs: FileSystem,
    workspace: str,
    name: str,
) -> Optional[str]:
    """The text of .clean-eval/<name> now, or None when there is no such file."""
    return text_or_none(fs, fs.join(workspace, REPO_DIR, name))


def original_tuning(
    fs: FileSystem,
    snapshot_dir: str,
    name: str,
) -> Optional[str]:
    """The text of .clean-eval/<name> in the snapshot, or None when there was no such file."""
    return text_or_none(fs, fs.join(snapshot_dir, name))


def restore_tuning(
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
    name: str,
) -> None:
    """Put .clean-eval/<name> back as it was in the snapshot (removed when it wasn't there)."""
    live, orig = fs.join(workspace, REPO_DIR, name), fs.join(snapshot_dir, name)
    if fs.is_file(orig):
        if not fs.is_file(live) or not fs.same_content(live, orig):
            clear_path(fs, live)
            fs.make_dirs(fs.parent(live))
            fs.copy_file(orig, live)
    else:
        clear_path(fs, live)


def restore_other_tuning(
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
    keep: Collection[str],
) -> list[str]:
    """Put every file under .clean-eval/ except `keep` back as it was; the paths that needed it, sorted."""
    live_dir, restored = fs.join(workspace, REPO_DIR), set()
    for top, in_snapshot in ((live_dir, False), (snapshot_dir, True)):
        for rel in fs.files_under(top):
            if rel in keep:
                continue
            live, orig = fs.join(live_dir, rel), fs.join(snapshot_dir, rel)
            if not in_snapshot and not fs.exists(orig):
                fs.remove(live)  # created by the agent
            elif not fs.exists(live) or not fs.same_content(live, orig):
                fs.make_dirs(fs.parent(live))
                fs.copy_file(orig, live)  # changed or removed by the agent
            else:
                continue
            restored.add(rel)
    return sorted(restored)


# Use case: collect the merged-PR history of a period and score how well the heat map and the review rules did,
# through the Git interface (and a CodeHost for review signals).
#
# Every PR is rated against the heat map of its *base* commit: the bands of the modules it touched are what its review saw.
# Later fixes and reverts touching the same files are attributed to it as escaped defects. With a code host, each PR's
# review threads give the rules its review cited and the clean-eval marker; fetch failures are recorded per PR.


def review_of(
    host: CodeHost,
    pr: int,
    reviewer_logins: Optional[list[str]] = None,
) -> Union[ReviewSignal, FetchFailure]:
    """The ReviewSignal of one PR, or a FetchFailure when its reviews couldn't be fetched."""
    try:
        view, inline = host.review_threads(pr)
    except RemoteError as e:
        return FetchFailure(
            error=e.kind,
            message=str(e),
        )
    return review_signal(view, inline, reviewer_logins)


def heatmap_at(
    git: Git,
    clock: Clock,
    rev: str,
    policy: Policy,
    cache: RatingCache,
) -> Heatmap:
    try:
        return rate_revision(git, clock, rev, policy, cache)
    except NoPythonSource:  # nothing to rate: every path a PR on this base touched is unrated
        return Heatmap(
            repo="",
            ref=rev,
            sha=rev[:7],
            generated_at="",
            loc_total=0,
        )


def merged_prs(
    git: Git,
    branch: str,
    period: Period,
) -> list[MergedPR]:
    """The PRs merged on `branch` from the start of `period` to the end of its look-ahead, oldest first."""
    log = git.first_parent_log(branch, period.since.isoformat(), period.lookahead_until.isoformat())
    return sorted(
        (
            MergedPR(
                sha=sha,
                base=parents[0],
                author=author,
                ts=ts,
                subject=subject,
                pr=pr_number(subject),
                files=git.diff_files(parents[0], sha),
            )
            for sha, parents, author, ts, subject in log
            if parents
        ),
        key=lambda merged_pr: merged_pr.ts,
    )


def rate_at_bases(
    git: Git,
    clock: Clock,
    prs: list[MergedPR],
    policy: Policy,
    cache: RatingCache,
) -> None:
    """Give each PR the risk of its change, from the heat map of its base commit (one rating per base)."""
    heatmaps = {}
    for u in prs:
        if u.base not in heatmaps:
            heatmaps[u.base] = heatmap_at(git, clock, u.base, policy, cache)
        risk = change_risk(heatmaps[u.base], u.files, policy)
        u.max_tier, u.max_score = risk.max_band, risk.max_score
        u.modules = [
            ModuleRecord(
                **m.model_dump(exclude={"evidence"}),
            )
            for m in risk.modules
        ]


def eval_history(
    prs: list[MergedPR],
    period: Period,
    branch: str,
    model_source: Optional[str],
    policy: Policy,
    rules: Optional[RuleStats],
) -> EvalHistory:
    """The EvalHistory of the period's rated `prs`, their defects attributed (and `rules`, the review rules' stats)."""
    defective = sum(1 for u in prs if u.defects)
    return EvalHistory(
        period=HistoryPeriod(
            since=period.since,
            until=period.until,
            branch=branch,
            model_source=model_source,
            defect_window_days=period.window_days,
            policy_version=policy.version,
        ),
        totals=Totals(
            prs=len(prs),
            defective_prs=defective,
            escape_rate=round(defective / len(prs), 3) if prs else None,
        ),
        calibration=calibration(prs),
        modules=module_stats(prs),
        rules=rules,
        prs=prs,
    )


def collect_history(
    git: Git,
    clock: Clock,
    policy: Policy,
    period: Period,
    host: Optional[CodeHost] = None,
    branch: Optional[str] = None,
    model_source: Optional[str] = None,
    reviewer_logins: Optional[list[str]] = None,
    cache: Optional[RatingCache] = None,
) -> EvalHistory:
    """The EvalHistory of the PRs merged in `period` (a Period) on `branch` (default: the repository's default branch).
    With a CodeHost, each PR's review signal too. `cache` (a RatingCache) shares the ratings' work across bases."""
    if host:
        host.require_auth()
    branch = branch or git.default_branch()
    units = merged_prs(git, branch, period)
    prs = in_period(units, period)
    rate_at_bases(git, clock, prs, policy, cache or RatingCache())
    attribute_defects(units, prs, period, policy)
    rules = None
    if host:
        for u in prs:
            if u.pr:
                u.review = review_of(host, u.pr, reviewer_logins)
        rules = rule_stats(prs)
    return eval_history(prs, period, branch, model_source, policy, rules)


# Use case: precompute everything the CI eval needs into the context folder `ctx`: the merged-PR history of the period
# with its escaped defects, one-knob sensitivity replays, the patches of the defects most worth verifying, and the tuning
# files as they are now (snapshotted, so finalizing can restore whatever it doesn't accept). Through the Git, FileSystem
# and Clock interfaces (and a CodeHost for review signals).

MAX_DEFECT_PATCHES = 30
# Which defect links get patches: highest bands first, then Low-band misses, then Moderate.
DEFECT_ORDER = frozendict({Band.CRITICAL: 0, Band.HIGH: 1, Band.LOW: 2, Band.MODERATE: 3})


def snapshot_current_tuning(
    fs: FileSystem,
    ctx: str,
    workspace: str,
    default_rules: str,
) -> tuple[Optional[str], Optional[str]]:
    """Keep the tuning as it is now in the context `ctx`: the risk model and the review rules (or their defaults), the
    policy's defaults, and a snapshot of .clean-eval/ that finalizing restores from. The model and rules files' texts,
    None where the repository has none."""
    model, rules = current_tuning(fs, workspace, MODEL_FILE), current_tuning(fs, workspace, RULES_FILE)
    fs.write_text(fs.join(ctx, "risk-model.orig.json"), model if model is not None else "{}")
    write_json(fs, fs.join(ctx, "risk-model.defaults.json"), Policy())
    snapshot_tuning(fs, workspace, fs.join(ctx, SNAPSHOT))
    fs.write_text(fs.join(ctx, "review-rules.orig.md"), rules if rules is not None else default_rules)
    return model, rules


def write_defect_patches(
    git: Git,
    fs: FileSystem,
    ctx: str,
    hist: EvalHistory,
) -> tuple[list[DefectPatch], int]:
    """Write the patches of the defect links most worth verifying (DEFECT_ORDER, at most MAX_DEFECT_PATCHES) into
    `ctx`/defects, with their index; the index, and how many links there were."""
    links = [(u, d) for u in hist.prs for d in u.defects]
    links.sort(
        key=lambda pr_and_defect: (
            DEFECT_ORDER.get(pr_and_defect[0].max_tier, 9),
            pr_and_defect[1].confidence != "high",
        )
    )
    index = []
    for i, (u, d) in enumerate(links[:MAX_DEFECT_PATCHES], 1):
        stem = "%02d-pr%s" % (i, u.pr or u.sha[:8])
        for kind, sha in (("pr", u.sha), ("fix", d.fix_sha)):
            patch, _ = truncate(git.commit_patch(sha, d.overlap_files), MAX_PATCH_CHARS)
            fs.write_text(fs.join(ctx, "defects", "%s-%s.patch" % (stem, kind)), patch)
        index.append(
            DefectPatch(
                file_stem=stem,
                pr=u.pr,
                pr_sha=u.sha,
                pr_band=u.max_tier,
                fix_sha=d.fix_sha,
                fix_subject=d.subject,
                kind=d.kind,
                confidence=d.confidence,
                overlap_files=d.overlap_files,
            )
        )
    write_json(fs, fs.join(ctx, "defects", "index.json"), index)
    return index, len(links)


def prepare_eval(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    ctx: str,
    workspace: str,
    policy: Policy,
    model_source: str,
    period: Period,
    default_rules: str,
    host: Optional[CodeHost] = None,
    branch: Optional[str] = None,
) -> EvalContext:
    """Write the eval context of `period` into `ctx`; its EvalContext."""
    model, rules = snapshot_current_tuning(fs, ctx, workspace, default_rules)
    hist = collect_history(git, clock, policy, period, host, branch, model_source)
    write_json(fs, fs.join(ctx, "eval-history.json"), hist)
    write_json(fs, fs.join(ctx, "sensitivity.json"), sensitivity(hist, policy))
    index, links = write_defect_patches(git, fs, ctx, hist)
    context = EvalContext(
        since=period.since,
        until=period.until,
        prs=hist.totals.prs,
        defective_prs=hist.totals.defective_prs,
        auc=hist.calibration.auc_max_score,
        model_source=model_source,
        model_existed=model is not None,
        rules_existed=rules is not None,
        with_gh=hist.rules is not None,
        rules_reliable=hist.rules.reliable if hist.rules else None,
        defect_patches=len(index),
        defect_links=links,
    )
    write_json(fs, fs.join(ctx, "context.json"), context)
    return context


def finalize_eval(
    fs: FileSystem,
    env: Environment,
    ctx: str,
    agent: str,
    out: str,
    apply: bool = False,
    workspace: str = ".",
) -> tuple[EvalResult, StepOutputs]:
    """(the EvalResult CI trusts, its StepOutputs) from what the CI agent proposed in `agent`. An edit of the tuning
    files is kept only with `apply` on, and only if it passes the checks of the domain and the eval's rules; everything
    else is restored. The edits are judged before the agent's output, so an invalid output never leaves an unchecked
    edit behind: when anything fails, every tuning file is restored. report.md and result.json go to `out`, redacted.
    InvalidOutput when the agent's output can't be trusted."""
    secrets = secret_values(env.variables())
    snapshot_dir = fs.join(ctx, SNAPSHOT)
    try:
        history = validated(EvalHistory, fs, ctx, "eval-history.json")
        model_change = judge_tuning_edit(
            fs, workspace, snapshot_dir, MODEL_FILE, apply, lambda new, orig: judge_model_edit(new, orig, history)
        )
        rules_change = judge_tuning_edit(
            fs, workspace, snapshot_dir, RULES_FILE, apply, lambda new, orig: judge_rules_edit(new)
        )
        other = restore_other_tuning(fs, workspace, snapshot_dir, TUNING_FILES)
        recommendations = eval_recommendations(read_json(fs, fs.join(agent, "result.json")), secrets)
        result, outputs = eval_result(history, recommendations, model_change, rules_change, other)
    except JUDGING_ERRORS as e:
        for name in sorted(TUNING_FILES):
            restore_tuning(fs, workspace, snapshot_dir, name)
        restore_other_tuning(fs, workspace, snapshot_dir, TUNING_FILES)
        raise invalid(e, " (any .clean-eval/ edits were reverted)")
    write_run_output(fs, out, fs.join(agent, "report.md"), result, secrets)
    return result, outputs


def judge_tuning_edit[T](
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
    name: str,
    apply: bool,
    judge: Callable[[Optional[str], Optional[str]], T],
) -> Optional[Union[T, Rejection]]:
    """The agent's change of one tuning file: kept when `judge` accepts it, else restored (a Rejection says why). None
    when the file is unchanged."""
    new, orig = current_tuning(fs, workspace, name), original_tuning(fs, snapshot_dir, name)
    if new == orig:
        return None
    try:
        if not apply:
            raise InvalidOutput(
                "apply=false",
            )
        return judge(new, orig)
    except JUDGING_ERRORS as e:
        restore_tuning(fs, workspace, snapshot_dir, name)
        return Rejection(
            reason=str(invalid(e)) if isinstance(e, ValidationError) else str(e),
        )


# Use case: the eval's CI run (the action's eval skill): collect the history of a period, let the agent evaluate it and,
# when allowed, edit the tuning files, then keep only the edits that pass the eval's checks. Through the Git,
# FileSystem, Clock, Environment, CodeHost, Agent and RunLog interfaces.

EVAL_PROMPT = (
    "Evaluate the merged PRs from %(since)s to %(until)s with apply=%(apply)s. Read %(context)s/skills/eval/SKILL.md "
    "and follow its 'CI mode' section. Precomputed context: %(context)s/context.json, %(context)s/eval-history.json, "
    "%(context)s/sensitivity.json, %(context)s/defects/index.json (patches alongside), %(context)s/risk-model.orig.json, "
    "%(context)s/review-rules.orig.md. Write %(agent)s/report.md and %(agent)s/result.json. %(extra)s"
)


def eval_period(
    clock: Clock,
    settings: Settings,
) -> Period:
    """The period an eval's run reads: from the settings' `since` (default: LOOKBACK_DAYS ago) to their `until`
    (default: today). RunFailed when it ends before it starts."""
    today = dt.date.fromisoformat(clock.today())
    try:
        return Period(
            since=settings.evaluation.since or today - dt.timedelta(days=LOOKBACK_DAYS),
            until=settings.evaluation.until or today,
        )
    except ValidationError as error:
        raise RunFailed.from_validation(error, "the eval's period")


def tuning_the_agent_may_edit(
    fs: FileSystem,
    folders: RunFolders,
    apply: bool,
) -> list[str]:
    """The folders the eval's agent may write besides its own: the workspace's .clean-eval/ with `apply`, else none.
    RunFailed when that folder is a symlink, whose target would be writable for the agent."""
    if not apply:
        return []
    tuning = fs.join(folders.workspace, REPO_DIR)
    if fs.is_link(tuning):
        raise RunFailed(
            "refusing apply=true: .clean-eval is a symlink",
        )
    return [tuning]


def signed_in(
    host: CodeHost,
) -> Optional[CodeHost]:
    """`host` when it is signed in, so the history can read the PRs' reviews; else None."""
    try:
        host.require_auth()
    except HeatmapError:
        return None
    return host


def report_eval(
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    period: Period,
) -> None:
    """Finalize what the eval's agent proposed, keeping only the edits that pass the eval's checks, and report the
    result's step outputs and its report in the job summary. RunFailed on agent output that can't be trusted (every
    edit is reverted then)."""
    apply = settings.evaluation.apply
    try:
        result, outputs = finalize_eval(fs, env, folders.context, folders.agent, folders.out, apply, folders.workspace)
    except InvalidOutput as error:
        raise RunFailed(
            "invalid agent output (any .clean-eval/ edits were reverted): %s" % error,
        )
    if result.redactions:
        log.warning("clean-eval redacted %d secret-looking value(s) from agent output" % result.redactions)
    write_step_outputs(fs, settings.github.step_outputs, outputs)
    report_run(fs, settings.github.step_summary, "Eval %s → %s" % (period.since, period.until), folders)


def run_eval_action(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    env: Environment,
    host: CodeHost,
    agent: Agent,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    default_rules: str,
) -> None:
    """The eval's CI run over the PRs merged in the settings' period (eval_period): collect their history (with their
    review signal when the code host is signed in), let the agent evaluate it and, when the settings say `apply`, edit
    .clean-eval/; then report what passes the eval's checks (report_eval). RunFailed on a period that ends before it
    starts, on a symlinked .clean-eval with `apply`, or on agent output that can't be trusted."""
    period = eval_period(clock, settings)
    writable = tuning_the_agent_may_edit(fs, folders, settings.evaluation.apply)
    reviews_host = signed_in(host)
    log.step("Collect the history from %s to %s" % (period.since, period.until))
    policy, source = load_policy(fs, folders.workspace)
    prepare_eval(
        git,
        fs,
        clock,
        folders.context,
        folders.workspace,
        policy,
        source,
        period,
        default_rules,
        host=reviews_host,
    )
    agent.require()
    prompt = EVAL_PROMPT % {
        "since": period.since,
        "until": period.until,
        "apply": "true" if settings.evaluation.apply else "false",
        "context": folders.context,
        "agent": folders.agent,
        "extra": settings.agent.extra_prompt,
    }
    run_agent(agent, fs, env, log, folders, prompt.strip(), writable)
    report_eval(fs, env, log, folders, settings, period)
