"""Merged-PR history: which PR a commit merged, which later fixes and reverts point back at it ("escaped defects"), and
what its clean-eval review cited. Each first-parent commit of the main branch is one merged PR (merge or squash). The
fix-to-PR links are heuristics (shared files plus fix/revert wording) that the eval skill verifies from the diffs."""
import re
from collections import defaultdict

from ...domain.history import DefectLink, FetchFailure, ModuleStats, ReviewSignal, RuleStats, RuleUse
from .classify import ignored
from .review import REVIEW_MARKER

RULE_ID = re.compile(r"\[([A-Z]{1,4}-\d{2,3}|NEW)\]")  # pr-review tags findings as [CA-01] / [NEW]
MERGE_PR = re.compile(r"Merge pull request #(\d+)")
SQUASH_PR = re.compile(r"\(#(\d+)\)")  # GitHub squash subjects end with "(#123)"; take the last one
FIX = re.compile(r"(?i)\b(fix(es|ed)?|bug|hotfix|regression|patch|revert)\b")
REVERT = re.compile(r"(?i)^revert\b")
GH_ERROR_RATE_LIMIT = 0.2  # above this share of failed fetches, rule metrics are flagged unreliable
DAY = 86400


def pr_number(subject):
    """The PR a first-parent commit merged, from its subject; None for a direct push."""
    m = MERGE_PR.search(subject)
    if m:
        return int(m.group(1))
    found = SQUASH_PR.findall(subject)
    return int(found[-1]) if found else None


def in_period(units, period):
    return [u for u in units if u.ts <= period.until_ts]


def attribute_defects(units, prs, period, policy):
    """Attach to each of `prs` the later fixes and reverts (from `units`, the period plus its look-ahead) that most likely
    repaired it: a fix or revert merged within the window that shares files with the PR. Of several such PRs one the fix
    names (its short sha, or the reverted subject) wins, else the most recent; `confidence` says which."""
    window = period.window_days * DAY
    for u in prs:
        u.defects = []
    for fix in units:
        is_revert = bool(REVERT.search(fix.subject))
        if not (is_revert or FIX.search(fix.subject)):
            continue
        fset = {f for f in fix.files if not ignored(f, policy)}
        candidates = [u for u in prs if u.sha != fix.sha and 0 < fix.ts - u.ts <= window and fset & set(u.files)]
        if not candidates:
            continue
        strong = [u for u in candidates if u.sha[:7] in fix.subject or (is_revert and u.subject in fix.subject)]
        target = (strong or candidates)[-1]
        target.defects.append(DefectLink(
            fix_sha=fix.sha, fix_pr=fix.pr, subject=fix.subject, kind="revert" if is_revert else "fix",
            confidence="high" if strong else "heuristic", days_after=round((fix.ts - target.ts) / DAY, 1),
            overlap_files=sorted(fset & set(target.files))[:20]))
    return prs


def is_reviewer(login, reviewer_logins=None):
    """Whose clean-eval markers count: the given logins, else any GitHub App bot (`name[bot]` logins can't be registered
    by users, so a commenter can't forge one)."""
    if not login:
        return False
    return login in reviewer_logins if reviewer_logins else login.endswith("[bot]")


def review_signal(view, inline, reviewer_logins=None):
    """What one PR's reviews said. `view` holds the reviews, comments, labels and size of the PR (as the code host gives
    them); `inline` its inline review comments."""
    posts = [((r.get("author") or {}).get("login"), r.get("body") or "") for r in view.get("reviews", [])]
    posts += [((c.get("author") or {}).get("login"), c.get("body") or "") for c in view.get("comments", [])]
    posts += [((c.get("user") or {}).get("login"), c.get("body") or "") for c in inline]
    marker, marker_authors, ignored_markers = {}, set(), 0
    for login, body in posts:
        for m in REVIEW_MARKER.finditer(body):
            if not is_reviewer(login, reviewer_logins):
                ignored_markers += 1  # a marker from anyone else is not trusted
                continue
            marker_authors.add(login)
            for kv in m.group(1).split():
                k, _, v = kv.partition("=")
                marker[k] = v
    # When a clean-eval review exists, measure rules by what *it* cited, not by third-party comments.
    cited_text = "\n".join(b for login, b in posts if not marker_authors or login in marker_authors)
    tags = RULE_ID.findall(cited_text)
    return ReviewSignal(url=view.get("url"), labels=[label.get("name") for label in view.get("labels", [])],
                        size=(view.get("additions") or 0) + (view.get("deletions") or 0),
                        review_comment_count=len([b for _, b in posts if b.strip()]),
                        rules_cited=sorted(set(tags) - {"NEW"}), new_findings=tags.count("NEW"),
                        clean_eval_marker=marker or None, untrusted_markers_ignored=ignored_markers)


def rule_stats(prs):
    """How the review rules did on the PRs whose reviews were fetched; unreliable when too many fetches failed."""
    fetched = [u for u in prs if isinstance(u.review, ReviewSignal)]
    failed = [u.pr for u in prs if isinstance(u.review, FetchFailure) and u.review.error == "error"]
    not_found = [u.pr for u in prs if isinstance(u.review, FetchFailure) and u.review.error == "not_found"]
    attempted = len(fetched) + len(failed)
    error_rate = round(len(failed) / attempted, 3) if attempted else 0.0
    per_rule = defaultdict(RuleUse)
    for u in fetched:
        for r in u.review.rules_cited:
            per_rule[r].prs_cited += 1
            per_rule[r].cited_in_defective_prs += bool(u.defects)
    return RuleStats(per_rule=dict(per_rule), prs_reviewed=len(fetched), prs_fetch_failed=failed, prs_not_found=not_found,
                     gh_error_rate=error_rate, reliable=error_rate <= GH_ERROR_RATE_LIMIT,
                     prs_with_clean_eval_review=sum(1 for u in fetched if u.review.clean_eval_marker),
                     new_findings=sum(u.review.new_findings for u in fetched),
                     defective_prs_with_no_rule_cited=[u.pr for u in fetched if u.defects and not u.review.rules_cited])


def rules_warning(rules):
    """A warning when some review fetches failed, or None."""
    failed = len(rules.prs_fetch_failed)
    if not failed:
        return None
    return "gh review fetch failed for %d/%d PRs (%d%%)%s" % (
        failed, rules.prs_reviewed + failed, round(rules.gh_error_rate * 100),
        "" if rules.reliable else " - rule metrics unreliable")


def module_stats(prs):
    """Per module, most escaped defects first: PRs that touched it, how many had defects, mean score, bands seen."""
    by_module = defaultdict(lambda: {"prs": 0, "defective_prs": 0, "scores": [], "bands": defaultdict(int)})
    for u in prs:
        for m in u.modules:
            bm = by_module[m.module]
            bm["prs"] += 1
            bm["defective_prs"] += bool(u.defects)
            if m.score is not None:
                bm["scores"].append(m.score)
            bm["bands"][m.band] += 1
    return {name: ModuleStats(prs=bm["prs"], defective_prs=bm["defective_prs"],
                              escape_rate=round(bm["defective_prs"] / bm["prs"], 3),
                              mean_score=round(sum(bm["scores"]) / len(bm["scores"]), 1) if bm["scores"] else None,
                              bands_seen=dict(bm["bands"]))
            for name, bm in sorted(by_module.items(), key=lambda kv: -kv[1]["defective_prs"])}
