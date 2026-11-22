"""Use cases of the PR review: the risk a change carries, the context the CI reviewer reads, and posting the review
CI trusts."""

from typing import Optional

from clean_eval.domain.errors import InvalidPolicy, NoPythonSource, RemoteError
from clean_eval.domain.policy import REPO_POLICY, REPO_RULES, Policy
from clean_eval.domain.rating import ChangeRisk, Heatmap, RatingCache
from clean_eval.domain.review import (
    AgentReview,
    ChangeHeader,
    DiffIndex,
    PatchEntry,
    PostOutcome,
    Review,
    ReviewContext,
    ReviewPost,
    RiskReport,
    StepOutputs,
)
from clean_eval.use_cases.heatmap import rate_revision, revision_snapshot
from clean_eval.use_cases.interfaces import Clock, CodeHost, Environment, FileSystem, Git
from clean_eval.use_cases.rules.review import (
    change_risk,
    commentable_lines,
    compose_review,
    finalize_review,
    line_counts,
    parse_rules,
    post_attempts,
    redact,
    review_outputs,
    rules_version,
    secret_values,
    split_patch,
    stale_reviews,
    truncate,
)
from clean_eval.use_cases.store import JUDGING_ERRORS, invalid, read_json, validated, write_json, write_run_output

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
# Config always comes from the PR's *base* commit (or the defaults), so a PR can't relax the rules or the risk model it is
# judged by.

MAX_HISTORY_FILES = 60
MAX_PATCH_CHARS = 200_000
DEFAULT_SOURCE = "clean-eval default"
PR_META = {"number", "title", "body", "url", "baseRefName", "headRefName", "isCrossRepository", "author"}


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
    combined diff is easily longer than one file read)."""
    chunks = split_patch(git.diff(base, head))
    if len(chunks) != len(files):  # unexpected shape (e.g. renames): fall back to per-path diffs
        chunks = [git.diff(base, head, f) for f in files]
    index = []
    for i, (f, chunk) in enumerate(zip(files, chunks), 1):
        text, cut = truncate(chunk, MAX_PATCH_CHARS)
        name = "diff/%03d.patch" % i
        fs.write_text(fs.join(ctx, name), text)
        added, removed = line_counts(chunk)
        index.append(
            PatchEntry(
                file=f,
                module=file_modules.get(f),
                patch=name,
                patch_lines=text.count("\n"),
                added=added,
                removed=removed,
                truncated=cut,
            )
        )
    write_json(fs, fs.join(ctx, "diff", "index.json"), DiffIndex(index))
    return index


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
    info = host.pull_request(pr)
    head = info.headRefOid
    git.ensure_commit(head, "pull/%d/head" % pr)
    git.ensure_commit(info.baseRefOid, info.baseRefOid)
    base = git.merge_base(info.baseRefOid, head)

    policy, model_src, model_text, model_note = policy_at(git, base)
    fs.write_text(fs.join(ctx, "risk-model.json"), model_text)
    rules = git.show(base, REPO_RULES)
    rules_src = "%s@%s" % (REPO_RULES, base[:8]) if rules is not None else DEFAULT_SOURCE
    rules = rules if rules is not None else default_rules
    fs.write_text(fs.join(ctx, "review-rules.md"), rules)

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
        rules_version=rules_version(rules),
        files_changed=len(files),
    )
    modules = [m.model_copy(update={"facts": None}) for m in risk.modules]  # the reviewer reads evidence, not facts
    notes = heatmap.notes + ([model_note] if model_note else [])
    write_json(
        fs,
        fs.join(ctx, "risk.json"),
        RiskReport(**header.model_dump(), modules=modules, files=risk.files, ignored=risk.ignored, notes=notes),
    )
    write_json(fs, fs.join(ctx, "pr.json"), {**info.model_dump(include=PR_META), "files": files})

    index = write_split_diff(git, fs, ctx, base, head, files, risk.files)
    context = ReviewContext(
        **header.model_dump(), diff_patches=len(index), diff_truncated=any(e.truncated for e in index)
    )

    lines = []
    for f in files[:MAX_HISTORY_FILES]:
        lines += ["## " + f, git.file_log(base, f, 10) or "(no history before this PR)", ""]
    if len(files) > MAX_HISTORY_FILES:
        lines.append("[history omitted for %d more files]" % (len(files) - MAX_HISTORY_FILES))
    fs.write_text(fs.join(ctx, "file-history.txt"), "\n".join(lines))

    write_json(fs, fs.join(ctx, "context.json"), context)
    return context


# Use case: post the validated review on its PR as one review, with the findings on diff lines attached inline, then
# dismiss this reviewer's earlier "changes requested" reviews, so a fixed PR isn't left blocked. Through the CodeHost,
# FileSystem and Environment interfaces (the environment names the secrets to redact from host errors).


def lines_on_diff(
    fs: FileSystem,
    ctx: Optional[str],
) -> Optional[dict[str, set[int]]]:
    """{path: new-side lines GitHub accepts inline comments on}, from the per-file patches in context folder `ctx`; None
    without them."""
    if ctx is None or not fs.exists(fs.join(ctx, "diff", "index.json")):
        return None
    index = DiffIndex.model_validate(read_json(fs, fs.join(ctx, "diff", "index.json"))).root
    return {e.file: commentable_lines(fs.read_text(fs.join(ctx, e.patch))) for e in index}


def post_review(
    host: CodeHost,
    fs: FileSystem,
    env: Environment,
    out: str,
    pr: int,
    head: str,
    ctx: Optional[str] = None,
) -> PostOutcome:
    """Post out/result.json and out/report.md as the review of PR `pr` at commit `head`; the PostOutcome."""
    secrets = secret_values(env.variables())

    def error(
        exception: RemoteError,
        limit: int,
    ) -> str:
        return redact(str(exception), secrets)[0][:limit]  # redact before cutting: host errors can echo request data

    outcome = PostOutcome()
    review = Review.model_validate(read_json(fs, fs.join(out, "result.json")))
    body, event, comments = compose_review(review, fs.read_text(fs.join(out, "report.md")), lines_on_diff(fs, ctx))
    posted = None
    for ev, cm in post_attempts(event, comments):
        try:
            posted = host.post_review(pr, ReviewPost(commit_id=head, body=body, event=ev, comments=cm))
        except RemoteError as e:
            outcome.log.append(
                ("warning", "review post failed (event=%s, comments=%d): %s" % (ev, len(cm), error(e, 300)))
            )
            continue
        outcome.posted = True
        outcome.log.append(("info", "posted review on #%s: event=%s, inline comments=%d" % (pr, ev, len(cm))))
        break
    if posted is None:
        return outcome

    try:
        reviews = host.reviews(pr)
    except RemoteError as e:
        outcome.log.append(("warning", "could not list reviews to dismiss stale ones: %s" % error(e, 200)))
        return outcome
    for review_id in stale_reviews(reviews, posted.user, posted.id):
        try:
            host.dismiss_review(pr, review_id, "Superseded by the clean-eval review of %s" % head[:8])
            outcome.log.append(("info", "dismissed stale review %s" % review_id))
        except RemoteError as e:
            outcome.log.append(("warning", "could not dismiss review %s: %s" % (review_id, error(e, 200))))
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
    secrets = secret_values(env.variables())
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
