"""Use cases of the retro: keep or restore the tuning files the CI agent edits, collect the merged-PR history of a
period, prepare the agent's context, and run the retro in CI."""

import datetime as dt
from collections.abc import (
    Callable,
    Collection,
    Mapping,
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
from clean_eval.domain.retro import (
    DefectPatch,
    FetchFailure,
    HistoryPeriod,
    MergedPR,
    ModelChange,
    Period,
    Rejection,
    RetroContext,
    RetroHistory,
    RetroResult,
    ReviewSignal,
    RulesChange,
    RuleStats,
    Totals,
)
from clean_eval.domain.review import (
    AgentTask,
    RunFolders,
    Skill,
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
    Runner,
)
from clean_eval.use_cases.rules.retro import (
    MODEL_FILE,
    RULE_PACKS,
    attribute_defects,
    calibration,
    in_period,
    judge_model_edit,
    judge_rules_edit,
    module_stats,
    retro_recommendations,
    retro_result,
    review_signal,
    rule_stats,
    sensitivity,
)
from clean_eval.use_cases.rules.review import (
    change_risk,
    combine_rule_packs,
    is_rule_pack,
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
)

# The repository's tuning files (`.clean-eval/` in the working tree) and the snapshot of them taken before the CI agent
# ran, through the FileSystem interface: what a retro may change, and how anything it may not keep is put back.

SNAPSHOT = "clean-eval.orig"  # in the context folder: .clean-eval/ as it was before the agent ran
RULES_ORIG = "rules.orig"  # in the context folder: the repository's own rule packs, as the agent found them
RULES_DEFAULTS = "rules.defaults"  # in the context folder: clean-eval's rule packs


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


def rule_pack_names(
    fs: FileSystem,
    folder: str,
) -> list[str]:
    """The file names of the rule packs in `folder` (<name>.md). Symlinks are skipped: one could point outside the
    checkout."""
    if fs.is_link(folder):
        return []
    return [
        fs.name(path)
        for path in fs.glob(folder, "*.md")
        if is_rule_pack(fs.name(path)) and fs.is_file(path) and not fs.is_link(path)
    ]


def rule_packs_in(
    fs: FileSystem,
    folder: str,
) -> dict[str, str]:
    """{file name: text} of the rule packs in `folder`."""
    return {name: fs.read_text(fs.join(folder, name)) for name in rule_pack_names(fs, folder)}


def rule_pack_files(
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
) -> list[str]:
    """The repository's rule packs, there now or in the snapshot, as paths in .clean-eval/ (rules/<name>.md)."""
    names = set(rule_pack_names(fs, fs.join(workspace, REPO_DIR, RULE_PACKS)))
    names |= set(rule_pack_names(fs, fs.join(snapshot_dir, RULE_PACKS)))
    return sorted("%s/%s" % (RULE_PACKS, name) for name in names)


# Use case: collect the merged-PR history of a period and score how well the heat map and the review rules did,
# through the Git and CodeHost interfaces.
#
# Every PR is rated against the heat map of its *base* commit: the bands of the modules it touched are what its review
# saw. The code host reads which PR a commit merged from its subject. Later fixes and reverts touching the same files
# are attributed to it as escaped defects. With the reviews fetched, each PR's review threads give the rules its review
# cited and the clean-eval marker; fetch failures are recorded per PR.


def review_of(
    host: CodeHost,
    pr: int,
    reviewer_logins: Optional[list[str]] = None,
) -> Union[ReviewSignal, FetchFailure]:
    """The ReviewSignal of one PR, or a FetchFailure when its reviews couldn't be fetched."""
    try:
        threads = host.review_threads(pr)
    except RemoteError as e:
        return FetchFailure(
            error=e.kind,
            message=str(e),
        )
    return review_signal(threads, reviewer_logins)


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
    host: CodeHost,
    branch: str,
    period: Period,
) -> list[MergedPR]:
    """The PRs merged on `branch` from the start of `period` to the end of its look-ahead, oldest first, each numbered
    as the code host reads its subject."""
    log = git.first_parent_log(branch, period.since.isoformat(), period.lookahead_until.isoformat())
    return sorted(
        (
            MergedPR(
                sha=sha,
                base=parents[0],
                author=author,
                ts=ts,
                subject=subject,
                pr=host.merged_pr(subject),
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


def retro_history(
    prs: list[MergedPR],
    period: Period,
    branch: str,
    model_source: Optional[str],
    policy: Policy,
    rules: Optional[RuleStats],
) -> RetroHistory:
    """The RetroHistory of the period's rated `prs`, their defects attributed (and `rules`, the review rules' stats)."""
    defective = sum(1 for u in prs if u.defects)
    return RetroHistory(
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
    host: CodeHost,
    clock: Clock,
    policy: Policy,
    period: Period,
    with_reviews: bool = False,
    branch: Optional[str] = None,
    model_source: Optional[str] = None,
    reviewer_logins: Optional[list[str]] = None,
    cache: Optional[RatingCache] = None,
) -> RetroHistory:
    """The RetroHistory of the PRs merged in `period` on `branch` (default: the repository's default branch), numbered
    by the code host; with `with_reviews`, each PR's review signal too. `cache` (a RatingCache) shares the ratings' work
    across bases."""
    if with_reviews:
        host.require_auth()
    branch = branch or git.default_branch()
    units = merged_prs(git, host, branch, period)
    prs = in_period(units, period)
    rate_at_bases(git, clock, prs, policy, cache or RatingCache())
    attribute_defects(units, prs, period, policy)
    rules = None
    if with_reviews:
        for u in prs:
            if u.pr:
                u.review = review_of(host, u.pr, reviewer_logins)
        rules = rule_stats(prs)
    return retro_history(prs, period, branch, model_source, policy, rules)


# Use case: precompute everything the CI retro needs into the context folder `ctx`: the merged-PR history of the period
# with its escaped defects, one-knob sensitivity replays, the patches of the defects most worth verifying, and the
# tuning files as they are now (snapshotted, so finalizing can restore whatever it doesn't accept). Through the Git,
# FileSystem, Clock and CodeHost interfaces.

MAX_DEFECT_PATCHES = 30
# Which defect links get patches: highest bands first, then Low-band misses, then Moderate.
DEFECT_ORDER = frozendict({Band.CRITICAL: 0, Band.HIGH: 1, Band.LOW: 2, Band.MODERATE: 3})


def snapshot_current_tuning(
    fs: FileSystem,
    ctx: str,
    workspace: str,
    default_packs: Mapping[str, str],
) -> tuple[Optional[str], list[str]]:
    """Keep the tuning as it is now in the context `ctx`: the risk model and the policy's defaults, the repository's
    rule packs and clean-eval's (`default_packs`, {file name: text}), and a snapshot of .clean-eval/ that finalizing
    restores from. The model's text (None where the repository has none), and the names of its packs."""
    model = current_tuning(fs, workspace, MODEL_FILE)
    packs = rule_packs_in(fs, fs.join(workspace, REPO_DIR, RULE_PACKS))
    fs.write_text(fs.join(ctx, "risk-model.orig.json"), model if model is not None else "{}")
    write_json(fs, fs.join(ctx, "risk-model.defaults.json"), Policy())
    snapshot_tuning(fs, workspace, fs.join(ctx, SNAPSHOT))
    fs.make_dirs(fs.join(ctx, RULES_ORIG))
    for folder, written in ((RULES_ORIG, packs), (RULES_DEFAULTS, default_packs)):
        for name, text in written.items():
            fs.write_text(fs.join(ctx, folder, name), text)
    return model, sorted(packs)


def write_defect_patches(
    git: Git,
    fs: FileSystem,
    ctx: str,
    hist: RetroHistory,
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


def prepare_retro(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    host: CodeHost,
    ctx: str,
    workspace: str,
    policy: Policy,
    model_source: str,
    period: Period,
    default_packs: Mapping[str, str],
    with_reviews: bool = False,
    branch: Optional[str] = None,
) -> RetroContext:
    """Write the retro context of `period` into `ctx` (with each PR's review signal when `with_reviews`), with
    clean-eval's rule packs `default_packs` ({file name: text}); its RetroContext."""
    model, packs = snapshot_current_tuning(fs, ctx, workspace, default_packs)
    hist = collect_history(git, host, clock, policy, period, with_reviews, branch, model_source)
    write_json(fs, fs.join(ctx, "retro-history.json"), hist)
    write_json(fs, fs.join(ctx, "sensitivity.json"), sensitivity(hist, policy))
    index, links = write_defect_patches(git, fs, ctx, hist)
    context = RetroContext(
        since=period.since,
        until=period.until,
        prs=hist.totals.prs,
        defective_prs=hist.totals.defective_prs,
        auc=hist.calibration.auc_max_score,
        model_source=model_source,
        model_existed=model is not None,
        repo_rule_packs=packs,
        with_gh=hist.rules is not None,
        rules_reliable=hist.rules.reliable if hist.rules else None,
        defect_patches=len(index),
        defect_links=links,
    )
    write_json(fs, fs.join(ctx, "context.json"), context)
    return context


def finalize_retro(
    fs: FileSystem,
    env: Environment,
    ctx: str,
    agent: str,
    out: str,
    apply: bool = False,
    workspace: str = ".",
) -> tuple[RetroResult, StepOutputs]:
    """(the RetroResult CI trusts, its StepOutputs) from what the CI agent proposed in `agent`. An edit of the tuning
    files is kept only with `apply` on, and only if it passes the checks of the domain and the retro's rules; everything
    else is restored. The edits are judged before the agent's output, so an invalid output never leaves an unchecked
    edit behind: when anything fails, every tuning file is restored. report.md and result.json go to `out`, redacted.
    InvalidOutput when the agent's output can't be trusted."""
    secrets = env.secrets()
    snapshot_dir = fs.join(ctx, SNAPSHOT)
    packs = rule_pack_files(fs, workspace, snapshot_dir)
    tuning = [MODEL_FILE, *packs]
    try:
        history = validated(RetroHistory, fs, ctx, "retro-history.json")
        model_change, rules_change = judge_tuning_edits(fs, ctx, workspace, packs, history, apply)
        other = restore_other_tuning(fs, workspace, snapshot_dir, tuning)
        recommendations = retro_recommendations(read_json(fs, fs.join(agent, "result.json")), secrets)
        result, outputs = retro_result(history, recommendations, model_change, rules_change, other)
    except JUDGING_ERRORS as e:
        for name in tuning:
            restore_tuning(fs, workspace, snapshot_dir, name)
        restore_other_tuning(fs, workspace, snapshot_dir, tuning)
        raise invalid(e, " (any .clean-eval/ edits were reverted)")
    write_run_output(fs, out, fs.join(agent, "report.md"), result, secrets)
    return result, outputs


def judge_tuning_edits(
    fs: FileSystem,
    ctx: str,
    workspace: str,
    packs: list[str],
    history: RetroHistory,
    apply: bool,
) -> tuple[Optional[Union[ModelChange, Rejection]], Optional[Union[RulesChange, Rejection]]]:
    """The agent's changes of the tuning files, each kept only with `apply` and when accepted: the risk model's on the
    collected `history`, and the rule packs' (`packs`, paths in .clean-eval/) over clean-eval's, from the context `ctx`.
    The model's change and the packs'."""
    snapshot_dir = fs.join(ctx, SNAPSHOT)
    model_change = judge_tuning_edit(
        fs,
        workspace,
        snapshot_dir,
        [MODEL_FILE],
        apply,
        lambda changed: judge_model_edit(
            current_tuning(fs, workspace, MODEL_FILE), original_tuning(fs, snapshot_dir, MODEL_FILE), history
        ),
    )
    rules_change = judge_tuning_edit(
        fs,
        workspace,
        snapshot_dir,
        packs,
        apply,
        lambda changed: judge_rules_edit(
            combine_rule_packs(
                rule_packs_in(fs, fs.join(ctx, RULES_DEFAULTS)),
                rule_packs_in(fs, fs.join(workspace, REPO_DIR, RULE_PACKS)),
            ),
            [name.removeprefix(RULE_PACKS + "/") for name in changed],
        ),
    )
    return model_change, rules_change


def judge_tuning_edit[T](
    fs: FileSystem,
    workspace: str,
    snapshot_dir: str,
    names: list[str],
    apply: bool,
    judge: Callable[[list[str]], T],
) -> Optional[Union[T, Rejection]]:
    """The agent's change of the tuning files `names` (paths in .clean-eval/): kept when `judge` accepts the changed
    ones, else they are restored (a Rejection says why). None when none of them changed."""
    changed = [name for name in names if current_tuning(fs, workspace, name) != original_tuning(fs, snapshot_dir, name)]
    if not changed:
        return None
    try:
        if not apply:
            raise InvalidOutput(
                "apply=false",
            )
        return judge(changed)
    except JUDGING_ERRORS as e:
        for name in changed:
            restore_tuning(fs, workspace, snapshot_dir, name)
        return Rejection(
            reason=str(invalid(e)) if isinstance(e, ValidationError) else str(e),
        )


# Use case: the retro's CI run (the action's retro skill): collect the history of a period, let the agent evaluate it and,
# when allowed, edit the tuning files, then keep only the edits that pass the retro's checks. Through the Git,
# FileSystem, Clock, Environment, CodeHost, Agent, Runner and RunLog interfaces.


def retro_task(
    period: Period,
    apply: bool,
) -> AgentTask:
    """What the agent is asked to do for the retro of `period`: evaluate the history from the precomputed context, into
    a report and its recommendations, editing the tuning files only with `apply`."""
    return AgentTask(
        skill=Skill.RETRO,
        goal="Evaluate the merged PRs from %s to %s with apply=%s." % (period.since, period.until, str(apply).lower()),
        inputs=(
            "context.json",
            "retro-history.json",
            "sensitivity.json",
            "defects/index.json (patches alongside)",
            "risk-model.orig.json",
            "rules.defaults/ and rules.orig/ (clean-eval's rule files, and the repository's own over them)",
        ),
        outputs=("report.md", "result.json"),
    )


def retro_period(
    clock: Clock,
    settings: Settings,
) -> Period:
    """The period a retro's run reads: from the settings' `since` (default: LOOKBACK_DAYS ago) to their `until`
    (default: today). RunFailed when it ends before it starts."""
    today = dt.date.fromisoformat(clock.today())
    try:
        return Period(
            since=settings.retro.since or today - dt.timedelta(days=LOOKBACK_DAYS),
            until=settings.retro.until or today,
        )
    except ValidationError as error:
        raise RunFailed.from_validation(error, "the retro's period")


def tuning_the_agent_may_edit(
    fs: FileSystem,
    folders: RunFolders,
    apply: bool,
) -> list[str]:
    """The folders the retro's agent may write besides its own: the workspace's .clean-eval/ with `apply`, else none.
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
) -> bool:
    """Whether the code host is signed in, so the history can read the PRs' reviews."""
    try:
        host.require_auth()
    except HeatmapError:
        return False
    return True


def report_retro(
    fs: FileSystem,
    env: Environment,
    runner: Runner,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    period: Period,
) -> None:
    """Finalize what the retro's agent proposed, keeping only the edits that pass the retro's checks, and report the
    result's step outputs and its report in the job summary to the runner. RunFailed on agent output that can't be
    trusted (every edit is reverted then)."""
    apply = settings.retro.apply
    try:
        result, outputs = finalize_retro(fs, env, folders.context, folders.agent, folders.out, apply, folders.workspace)
    except InvalidOutput as error:
        raise RunFailed(
            "invalid agent output (any .clean-eval/ edits were reverted): %s" % error,
        )
    if result.redactions:
        log.warning("clean-eval redacted %d secret-looking value(s) from agent output" % result.redactions)
    report_run(fs, runner, folders, "Retro %s → %s" % (period.since, period.until), outputs)


def run_retro_action(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    env: Environment,
    host: CodeHost,
    agent: Agent,
    runner: Runner,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    default_packs: Mapping[str, str],
) -> None:
    """The retro's CI run over the PRs merged in the settings' period (retro_period): collect their history (with their
    review signal when the code host is signed in, and clean-eval's rule packs `default_packs`), let the agent evaluate
    it and, when the settings say `apply`, edit .clean-eval/; then report what passes the retro's checks
    (report_retro). RunFailed on a period that ends before it starts, on a symlinked .clean-eval with `apply`, or on
    agent output that can't be trusted."""
    period = retro_period(clock, settings)
    writable = tuning_the_agent_may_edit(fs, folders, settings.retro.apply)
    with_reviews = signed_in(host)
    log.step("Collect the history from %s to %s" % (period.since, period.until))
    policy, source = load_policy(fs, folders.workspace)
    prepare_retro(
        git,
        fs,
        clock,
        host,
        folders.context,
        folders.workspace,
        policy,
        source,
        period,
        default_packs,
        with_reviews=with_reviews,
    )
    agent.require()
    run_agent(agent, fs, env, log, folders, retro_task(period, settings.retro.apply), writable)
    report_retro(fs, env, runner, log, folders, settings, period)
