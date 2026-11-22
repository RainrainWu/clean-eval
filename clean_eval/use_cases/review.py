"""Use cases of the PR review: the risk a change carries, the context the CI reviewer reads, posting the review CI
trusts, and the review's run in CI."""

from typing import Optional

from clean_eval.domain.errors import (
    InvalidOutput,
    InvalidPolicy,
    NoPythonSource,
    RemoteError,
    RunFailed,
)
from clean_eval.domain.policy import (
    REPO_POLICY,
    REPO_RULES,
    Policy,
)
from clean_eval.domain.rating import (
    ChangeRisk,
    Heatmap,
    RatingCache,
)
from clean_eval.domain.review import (
    AgentReview,
    AgentTask,
    ChangeHeader,
    DiffIndex,
    HostReview,
    LogLevel,
    PatchEntry,
    PostOutcome,
    PullRequest,
    Review,
    ReviewContext,
    ReviewPost,
    RiskReport,
    RunFolders,
    Secrets,
    Skill,
    StepOutputs,
    Verdict,
)
from clean_eval.settings import (
    FailOn,
    Settings,
)
from clean_eval.use_cases.ci import (
    MAX_PATCH_CHARS,
    report_run,
    run_agent,
)
from clean_eval.use_cases.heatmap import (
    rate_revision,
    revision_snapshot,
)
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
from clean_eval.use_cases.rules.review import (
    change_risk,
    compose_review,
    finalize_review,
    parse_rules,
    redact,
    review_outputs,
    stale_reviews,
    truncate,
)
from clean_eval.use_cases.store import (
    JUDGING_ERRORS,
    invalid,
    read_json,
    validated,
    write_json,
    write_run_output,
)

# Use case: the risk a change carries, from the heat map of its base. The PR review scales its depth by this.


def assess_change(
    git: Git,
    clock: Clock,
    base: str,
    head: str,
    policy: Policy,
    cache: Optional[RatingCache] = None,
) -> tuple[Heatmap, ChangeRisk]:
    """(heat map at the base, ChangeRisk of base..head). A base with no Python yet has an empty heat map: every path is
    unrated."""
    try:
        heatmap = rate_revision(git, clock, base, policy, cache or RatingCache())
    except NoPythonSource as e:
        snap = revision_snapshot(git, base)
        heatmap = Heatmap(
            repo=snap.repo,
            ref=snap.ref,
            sha=snap.sha,
            generated_at=clock.today(),
            loc_total=0,
            notes=[str(e)],
            policy_version=policy.version,
        )
    return heatmap, change_risk(heatmap, git.diff_files(base, head), policy)


# Use case: precompute everything the CI reviewer needs, so it runs with no shell, no network and no token: the PR's
# metadata, its diff (one patch per file), per-file history, the risk the change carries (from the heat map of its base)
# and the base's config. Through the CodeHost, Git, FileSystem and Clock interfaces, into the context folder `ctx`.
#
# Config always comes from the PR's *base* commit (or the defaults), so a PR can't relax the rules or the risk model it
# is judged by.

MAX_HISTORY_FILES = 60
DEFAULT_SOURCE = "clean-eval default"
PR_META = frozenset({"number", "title", "body", "url", "base_ref", "head_ref", "cross_repository", "author"})


def policy_at(
    git: Git,
    base: str,
) -> tuple[Policy, str, str, Optional[str]]:
    """(policy, source, overrides text, note) from the base commit's risk-model overrides, else the defaults. An invalid
    file at the base falls back to the defaults with a note, rather than failing every review."""
    text = git.show(base, REPO_POLICY)
    if text is None:
        return Policy(), DEFAULT_SOURCE, "{}", None
    try:
        return Policy.from_text(text, REPO_POLICY), "%s@%s" % (REPO_POLICY, base[:8]), text, None
    except InvalidPolicy as e:
        return Policy(), DEFAULT_SOURCE, "{}", "ignored %s at the base: %s" % (REPO_POLICY, e)


def write_split_diff(
    git: Git,
    fs: FileSystem,
    ctx: str,
    base: str,
    head: str,
    files: list[str],
    file_modules: dict[str, Optional[str]],
) -> list[PatchEntry]:
    """One patch per changed file plus diff/index.json in `ctx`, so a reviewer can read every hunk in full (a single
    combined diff is easily longer than one file read). The index keeps the lines each diff shows, where inline
    comments can go."""
    index = []
    for i, diff in enumerate(git.file_diffs(base, head, files), 1):
        text, cut = truncate(diff.patch, MAX_PATCH_CHARS)
        name = "diff/%03d.patch" % i
        fs.write_text(fs.join(ctx, name), text)
        index.append(
            PatchEntry(
                file=diff.path,
                module=file_modules.get(diff.path),
                patch=name,
                patch_lines=text.count("\n"),
                added=diff.added,
                removed=diff.removed,
                truncated=cut,
                hunks=diff.hunks,
            )
        )
    write_json(
        fs,
        fs.join(ctx, "diff", "index.json"),
        DiffIndex(
            index,
        ),
    )
    return index


def pull_request_commits(
    host: CodeHost,
    git: Git,
    pr: int,
) -> tuple[PullRequest, str, str]:
    """PR `pr` as the code host shows it, with its commits fetched: the PR, its merge base and its head."""
    info = host.pull_request(pr)
    git.ensure_commit(info.head_sha, info.head_refspec)
    git.ensure_commit(info.base_sha, info.base_sha)
    return info, git.merge_base(info.base_sha, info.head_sha), info.head_sha


def write_rules_at_base(
    git: Git,
    fs: FileSystem,
    ctx: str,
    base: str,
    default_rules: str,
) -> tuple[str, str]:
    """Write the review rules the change is judged by into `ctx`: the base commit's .clean-eval/review-rules.md, else
    `default_rules`. Their text, and where they come from."""
    rules = git.show(base, REPO_RULES)
    source = "%s@%s" % (REPO_RULES, base[:8]) if rules is not None else DEFAULT_SOURCE
    rules = rules if rules is not None else default_rules
    fs.write_text(fs.join(ctx, "review-rules.md"), rules)
    return rules, source


def write_risk_report(
    fs: FileSystem,
    ctx: str,
    header: ChangeHeader,
    heatmap: Heatmap,
    risk: ChangeRisk,
    model_note: Optional[str],
) -> None:
    """Write the change's risk into `ctx`/risk.json: the header, the modules it touches (their evidence, not their
    facts: the reviewer reads evidence) and the band of each file, with the heat map's notes."""
    write_json(
        fs,
        fs.join(ctx, "risk.json"),
        RiskReport(
            **header.model_dump(),
            modules=[m.model_copy(update={"facts": None}) for m in risk.modules],
            files=risk.files,
            ignored=risk.ignored,
            notes=heatmap.notes + ([model_note] if model_note else []),
        ),
    )


def write_file_history(
    git: Git,
    fs: FileSystem,
    ctx: str,
    base: str,
    files: list[str],
) -> None:
    """Write the history of the changed `files` before the change (at most MAX_HISTORY_FILES) into `ctx`."""
    lines = []
    for f in files[:MAX_HISTORY_FILES]:
        lines += ["## " + f, git.file_log(base, f, 10) or "(no history before this PR)", ""]
    if len(files) > MAX_HISTORY_FILES:
        lines.append("[history omitted for %d more files]" % (len(files) - MAX_HISTORY_FILES))
    fs.write_text(fs.join(ctx, "file-history.txt"), "\n".join(lines))


def prepare_review(
    host: CodeHost,
    git: Git,
    fs: FileSystem,
    clock: Clock,
    ctx: str,
    pr: int,
    default_rules: str,
) -> ReviewContext:
    """Write the review context of PR `pr` into `ctx`; its ReviewContext. `default_rules` is the rules text used when
    the base has no .clean-eval/review-rules.md."""
    info, base, head = pull_request_commits(host, git, pr)
    policy, model_src, model_text, model_note = policy_at(git, base)
    fs.write_text(fs.join(ctx, "risk-model.json"), model_text)
    rules, rules_src = write_rules_at_base(git, fs, ctx, base, default_rules)
    heatmap, risk = assess_change(git, clock, base, head, policy)
    files = list(risk.files)
    header = ChangeHeader(
        pr=pr,
        base=base,
        head=head,
        max_tier=risk.max_band,
        max_score=risk.max_score,
        model_source=model_src,
        model_version=policy.version,
        rules_source=rules_src,
        rules_version=git.blob_id(rules)[:8],
        files_changed=len(files),
    )
    write_risk_report(fs, ctx, header, heatmap, risk, model_note)
    write_json(fs, fs.join(ctx, "pr.json"), {**info.model_dump(include=set(PR_META)), "files": files})
    index = write_split_diff(git, fs, ctx, base, head, files, risk.files)
    write_file_history(git, fs, ctx, base, files)
    context = ReviewContext(
        **header.model_dump(),
        diff_patches=len(index),
        diff_truncated=any(e.truncated for e in index),
    )
    write_json(fs, fs.join(ctx, "context.json"), context)
    return context


# Use case: post the validated review on its PR as one review, with the findings on diff lines attached inline, then
# dismiss this reviewer's earlier "changes requested" reviews, so a fixed PR isn't left blocked. Through the CodeHost,
# FileSystem and Environment interfaces (the environment names the secrets to redact from host errors).


def lines_on_diff(
    fs: FileSystem,
    ctx: Optional[str],
) -> Optional[dict[str, set[int]]]:
    """{path: the lines of the file as the change leaves it that are in its diff, where inline comments can go}, from
    the diff's index in context folder `ctx`; None without it."""
    if ctx is None or not fs.exists(fs.join(ctx, "diff", "index.json")):
        return None
    index = DiffIndex.model_validate(read_json(fs, fs.join(ctx, "diff", "index.json"))).root
    return {e.file: {line for first, last in e.hunks for line in range(first, last + 1)} for e in index}


def host_error_text(
    error: str,
    secrets: Secrets,
    limit: int,
) -> str:
    """A code host's error as a log line can show it: redacted of `secrets`, then cut to `limit` characters (redacted
    first, since a host's error can echo the request's data)."""
    return redact(error, secrets)[0][:limit]


def post_on_the_host(
    host: CodeHost,
    pr: int,
    head: str,
    review: Review,
    report: str,
    lines: Optional[dict[str, set[int]]],
    secrets: Secrets,
    outcome: PostOutcome,
) -> Optional[HostReview]:
    """Post the review of PR `pr` at `head`, as much of it as the code host takes; the posted review, or None. Each
    attempt is logged in `outcome`, its errors redacted."""
    body, request_changes, comments = compose_review(review, report, lines)
    posting = host.post_review(
        pr,
        ReviewPost(
            commit=head,
            body=body,
            request_changes=request_changes,
            comments=comments,
        ),
    )
    for attempt in posting.attempts:
        if attempt.error is None:
            outcome.log.append((LogLevel.INFO, "posted review on #%s: %s" % (pr, attempt.tried)))
        else:
            error = host_error_text(attempt.error, secrets, 300)
            outcome.log.append((LogLevel.WARNING, "review post failed (%s): %s" % (attempt.tried, error)))
    outcome.posted = posting.review is not None
    return posting.review


def dismiss_stale_reviews(
    host: CodeHost,
    pr: int,
    head: str,
    posted: HostReview,
    secrets: Secrets,
    outcome: PostOutcome,
) -> None:
    """Dismiss this reviewer's earlier reviews of PR `pr` that request changes, superseded by `posted`, so a fixed PR
    isn't left blocked. Each step is logged in `outcome`."""
    try:
        reviews = host.reviews(pr)
    except RemoteError as e:
        error = host_error_text(str(e), secrets, 200)
        outcome.log.append((LogLevel.WARNING, "could not list reviews to dismiss stale ones: %s" % error))
        return
    for review_id in stale_reviews(reviews, posted.author, posted.id):
        try:
            host.dismiss_review(pr, review_id, "Superseded by the clean-eval review of %s" % head[:8])
            outcome.log.append((LogLevel.INFO, "dismissed stale review %s" % review_id))
        except RemoteError as e:
            error = host_error_text(str(e), secrets, 200)
            outcome.log.append((LogLevel.WARNING, "could not dismiss review %s: %s" % (review_id, error)))


def post_review(
    host: CodeHost,
    fs: FileSystem,
    env: Environment,
    out: str,
    pr: int,
    head: str,
    ctx: Optional[str] = None,
) -> PostOutcome:
    """Post out/result.json and out/report.md as the review of PR `pr` at commit `head`, and dismiss the reviews it
    supersedes; the PostOutcome."""
    secrets = env.secrets()
    outcome = PostOutcome()
    review = Review.model_validate(read_json(fs, fs.join(out, "result.json")))
    report = fs.read_text(fs.join(out, "report.md"))
    posted = post_on_the_host(host, pr, head, review, report, lines_on_diff(fs, ctx), secrets, outcome)
    if posted is not None:
        dismiss_stale_reviews(host, pr, head, posted, secrets, outcome)
    return outcome


def finalize_pr_review(
    fs: FileSystem,
    env: Environment,
    ctx: str,
    agent: str,
    out: str,
) -> tuple[Review, StepOutputs]:
    """(the Review CI trusts, its StepOutputs) from what the CI agent proposed in `agent`: the domain models check the
    findings' shape, and the review rules hold them to the rules table and derive the verdict, blocker count, band and
    score. report.md and result.json go to `out`, redacted (the environment names the secrets). InvalidOutput when the
    agent's output can't be trusted; nothing is written then."""
    secrets = env.secrets()
    try:
        review = finalize_review(
            validated(AgentReview, fs, agent, "result.json"),
            validated(RiskReport, fs, ctx, "risk.json"),
            parse_rules(fs.read_text(fs.join(ctx, "review-rules.md"))),
            secrets,
        )
    except JUDGING_ERRORS as e:
        raise invalid(e)
    outputs = review_outputs(review)
    write_run_output(fs, out, fs.join(agent, "report.md"), review, secrets)
    return review, outputs


# Use case: the PR review's CI run (the action's pr-review skill): prepare the context, let the agent review the
# change, finalize the review CI trusts, post it, and fail the run as configured. Through the CodeHost, Git,
# FileSystem, Clock, Environment, Agent, Runner and RunLog interfaces.


def review_task(
    pr: int,
) -> AgentTask:
    """What the agent is asked to do for the review of PR `pr`: review it from the precomputed context, into a report
    and the findings it proposes."""
    return AgentTask(
        skill=Skill.PR_REVIEW,
        goal="Review pull request #%d." % pr,
        inputs=(
            "context.json",
            "pr.json",
            "risk.json",
            "review-rules.md",
            "diff/index.json (one patch per changed file — read them all)",
            "file-history.txt",
        ),
        outputs=("report.md", "result.json"),
    )


def trusted_review(
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    fail_on: FailOn,
) -> Optional[tuple[Review, StepOutputs]]:
    """The review CI trusts, finalized from what the agent proposed, and its step outputs. Agent output that can't be
    trusted is RunFailed, failing closed, unless `fail_on` is never: then it is a warning, and None."""
    try:
        return finalize_pr_review(fs, env, folders.context, folders.agent, folders.out)
    except InvalidOutput as error:
        if fail_on == FailOn.NEVER:
            log.warning("invalid agent output, so nothing is posted: %s" % error)
            return None
        raise RunFailed(
            "invalid agent output with fail-on=%s (failing closed): %s" % (fail_on, error),
        )


def post_and_log(
    host: CodeHost,
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    pr: int,
    head: str,
) -> None:
    """Post the run's review on PR `pr` at `head`, and log how it went."""
    posted = post_review(host, fs, env, folders.out, pr, head, folders.context)
    for level, message in posted.log:
        if level == "warning":
            log.warning(message)
        else:
            log.show(message)
    if not posted.posted:
        log.warning("could not post the review")


def fail_as_told(
    review: Review,
    fail_on: FailOn,
) -> None:
    """RunFailed as `fail_on` says: `request_changes` on that verdict, `blocker` on a blocker finding."""
    if fail_on == FailOn.REQUEST_CHANGES and review.verdict == Verdict.REQUEST_CHANGES:
        raise RunFailed(
            "review verdict: request_changes",
        )
    if fail_on == FailOn.BLOCKER and review.blockers:
        raise RunFailed(
            "%d blocker finding(s)" % review.blockers,
        )


def run_review_action(
    host: CodeHost,
    git: Git,
    fs: FileSystem,
    clock: Clock,
    env: Environment,
    agent: Agent,
    runner: Runner,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    default_rules: str,
) -> None:
    """The PR review's CI run for the settings' PR: prepare its context, let the agent review it, finalize the review
    CI trusts, post it when the settings say `post`, and report it to the runner. RunFailed as the settings' `fail_on`
    says (fail_as_told), and on agent output that can't be trusted unless `fail_on` is never (trusted_review)."""
    pr, fail_on = settings.review.pr, settings.review.fail_on
    if pr is None:
        raise RunFailed(
            "pr-review needs a PR number (input 'pr')",
        )
    log.step("Prepare the review context of #%d" % pr)
    context = prepare_review(host, git, fs, clock, folders.context, pr, default_rules)
    agent.require()
    run_agent(agent, fs, env, log, folders, review_task(pr))
    trusted = trusted_review(fs, env, log, folders, fail_on)
    if trusted is None:
        return
    review, outputs = trusted
    if review.redactions:
        log.warning("clean-eval redacted %d secret-looking value(s) from agent output" % review.redactions)
    report_run(fs, runner, folders, "PR Review #%d" % pr, outputs)
    if settings.review.post:
        post_and_log(host, fs, env, log, folders, pr, context.head)
    fail_as_told(review, fail_on)
