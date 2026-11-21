"""Use case: precompute everything the CI reviewer needs, so it runs with no shell, no network and no token: the PR's
metadata, its diff (one patch per file), per-file history, the risk the change carries (from the heat map of its base)
and the base's config.

Config always comes from the PR's *base* commit (or the defaults), so a PR can't relax the rules or the risk model it is
judged by."""
from ..domain.errors import InvalidPolicy
from ..domain.policy import REPO_POLICY, REPO_RULES, Policy
from ..domain.review import DiffIndex, PatchEntry, ReviewContext, RiskReport
from .rules.diff import line_counts, split_patch, truncate
from .rules.review import rules_version

MAX_HISTORY_FILES = 60
MAX_PATCH_CHARS = 200_000
DEFAULT_SOURCE = "clean-eval default"
PR_META = {"number", "title", "body", "url", "baseRefName", "headRefName", "isCrossRepository", "author"}


def policy_at(repo, base):
    """(policy, source, overrides text, note) from the base commit's risk-model overrides, else the defaults. An invalid
    file at the base falls back to the defaults with a note, rather than failing every review."""
    text = repo.show(base, REPO_POLICY)
    if text is None:
        return Policy(), DEFAULT_SOURCE, "{}", None
    try:
        return Policy.from_text(text, REPO_POLICY), "%s@%s" % (REPO_POLICY, base[:8]), text, None
    except InvalidPolicy as e:
        return Policy(), DEFAULT_SOURCE, "{}", "ignored %s at the base: %s" % (REPO_POLICY, e)


def write_split_diff(repo, ctx, base, head, files, file_modules):
    """One patch per changed file plus diff/index.json, so a reviewer can read every hunk in full (a single combined diff
    is easily longer than one file read)."""
    chunks = split_patch(repo.diff(base, head))
    if len(chunks) != len(files):  # unexpected shape (e.g. renames): fall back to per-path diffs
        chunks = [repo.diff(base, head, f) for f in files]
    index = []
    for i, (f, chunk) in enumerate(zip(files, chunks), 1):
        text, cut = truncate(chunk, MAX_PATCH_CHARS)
        name = "diff/%03d.patch" % i
        ctx.write_text(name, text)
        added, removed = line_counts(chunk)
        index.append(PatchEntry(file=f, module=file_modules.get(f), patch=name, patch_lines=text.count("\n"),
                                added=added, removed=removed, truncated=cut))
    ctx.write_json("diff/index.json", DiffIndex(index))
    return index


class PrepareReview:
    def __init__(self, host, repo, risk, ctx, default_rules):
        self.host, self.repo, self.risk, self.ctx, self.default_rules = host, repo, risk, ctx, default_rules

    def execute(self, pr):
        info = self.host.pull_request(pr)
        head = info.headRefOid
        self.repo.ensure_commit(head, "pull/%d/head" % pr)
        self.repo.ensure_commit(info.baseRefOid, info.baseRefOid)
        base = self.repo.merge_base(info.baseRefOid, head)

        policy, model_src, model_text, model_note = policy_at(self.repo, base)
        self.ctx.write_text("risk-model.json", model_text)
        rules = self.repo.show(base, REPO_RULES)
        rules_src = "%s@%s" % (REPO_RULES, base[:8]) if rules is not None else DEFAULT_SOURCE
        rules = rules if rules is not None else self.default_rules
        self.ctx.write_text("review-rules.md", rules)

        heatmap, risk = self.risk.assess(base, head, policy)
        files = list(risk.files)
        header = dict(pr=pr, base=base, head=head, max_tier=risk.max_band, max_score=risk.max_score, model_source=model_src,
                      model_version=policy.version, rules_source=rules_src, rules_version=rules_version(rules),
                      files_changed=len(files))
        modules = [m.model_copy(update={"facts": None}) for m in risk.modules]  # the reviewer reads evidence, not facts
        notes = heatmap.notes + ([model_note] if model_note else [])
        self.ctx.write_json("risk.json", RiskReport(**header, modules=modules, files=risk.files, ignored=risk.ignored,
                                                    notes=notes))
        self.ctx.write_json("pr.json", {**info.model_dump(include=PR_META), "files": files})

        index = write_split_diff(self.repo, self.ctx, base, head, files, risk.files)
        context = ReviewContext(**header, diff_patches=len(index), diff_truncated=any(e.truncated for e in index))

        lines = []
        for f in files[:MAX_HISTORY_FILES]:
            lines += ["## " + f, self.repo.file_log(base, f, 10) or "(no history before this PR)", ""]
        if len(files) > MAX_HISTORY_FILES:
            lines.append("[history omitted for %d more files]" % (len(files) - MAX_HISTORY_FILES))
        self.ctx.write_text("file-history.txt", "\n".join(lines))

        self.ctx.write_json("context.json", context)
        return context
