"""Use case: precompute everything the CI reviewer needs, so it runs with no shell, no network and no token: the PR's
metadata, its diff (one patch per file), per-file history, the risk the change carries (from the heat map of its base)
and the base's config. Through the CodeHost, Git, FileSystem and Clock interfaces, into the context folder `ctx`.

Config always comes from the PR's *base* commit (or the defaults), so a PR can't relax the rules or the risk model it is
judged by."""

from clean_eval.domain.errors import InvalidPolicy
from clean_eval.domain.policy import REPO_POLICY, REPO_RULES, Policy
from clean_eval.domain.review import DiffIndex, PatchEntry, ReviewContext, RiskReport
from clean_eval.use_cases.assess_change import assess_change
from clean_eval.use_cases.rules.diff import line_counts, split_patch, truncate
from clean_eval.use_cases.rules.review import rules_version
from clean_eval.use_cases.store import write_json

MAX_HISTORY_FILES = 60
MAX_PATCH_CHARS = 200_000
DEFAULT_SOURCE = "clean-eval default"
PR_META = {"number", "title", "body", "url", "baseRefName", "headRefName", "isCrossRepository", "author"}


def policy_at(git, base):
    """(policy, source, overrides text, note) from the base commit's risk-model overrides, else the defaults. An invalid
    file at the base falls back to the defaults with a note, rather than failing every review."""
    text = git.show(base, REPO_POLICY)
    if text is None:
        return Policy(), DEFAULT_SOURCE, "{}", None
    try:
        return Policy.from_text(text, REPO_POLICY), "%s@%s" % (REPO_POLICY, base[:8]), text, None
    except InvalidPolicy as e:
        return Policy(), DEFAULT_SOURCE, "{}", "ignored %s at the base: %s" % (REPO_POLICY, e)


def write_split_diff(git, fs, ctx, base, head, files, file_modules):
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


def prepare_review(host, git, fs, clock, ctx, pr, default_rules):
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
    header = dict(
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
        RiskReport(**header, modules=modules, files=risk.files, ignored=risk.ignored, notes=notes),
    )
    write_json(fs, fs.join(ctx, "pr.json"), {**info.model_dump(include=PR_META), "files": files})

    index = write_split_diff(git, fs, ctx, base, head, files, risk.files)
    context = ReviewContext(**header, diff_patches=len(index), diff_truncated=any(e.truncated for e in index))

    lines = []
    for f in files[:MAX_HISTORY_FILES]:
        lines += ["## " + f, git.file_log(base, f, 10) or "(no history before this PR)", ""]
    if len(files) > MAX_HISTORY_FILES:
        lines.append("[history omitted for %d more files]" % (len(files) - MAX_HISTORY_FILES))
    fs.write_text(fs.join(ctx, "file-history.txt"), "\n".join(lines))

    write_json(fs, fs.join(ctx, "context.json"), context)
    return context
