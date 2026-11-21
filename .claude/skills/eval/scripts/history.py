#!/usr/bin/env python3
"""Collect merged-PR history for a period and score how well the risk heat map and the review rules did.

Each first-parent commit on the main branch is one merged PR (merge or squash). Every PR is rated against the heat map
of its *base* commit (clean_eval, static analysis of that revision with the built-in graph): the bands of the modules
it touched are what the PR review saw. Then we look ahead `--defect-window-days` for fix/revert commits that touch the
same files - "escaped defects", a heuristic the eval skill verifies from the diffs.

With --with-gh, PR review threads are fetched with the GitHub CLI and the rule tags cited in them ([CA-01], [Q-02],
[NEW]) and the pr-review marker are extracted. Fetch failures are recorded per PR; rule metrics are flagged unreliable
when too many fail.

Replays (--replay / --sensitivity) re-score the stored per-module facts under a candidate policy (risk-model overrides),
so weight changes are evaluated in milliseconds without touching git. Output: JSON on stdout (or --out).
"""
import argparse
import dataclasses
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path



def clean_eval_home():
    """The checkout that holds the clean_eval package: $CLEAN_EVAL_HOME, else a folder above this script (the skill
    inside the checkout), else the current folder. A skill installed elsewhere (e.g. ~/.claude/skills) needs the env var."""
    env = os.environ.get("CLEAN_EVAL_HOME")
    for candidate in ([Path(env)] if env else []) + list(Path(__file__).resolve().parents) + [Path.cwd()]:
        if (candidate / "clean_eval" / "__init__.py").is_file():
            return candidate.resolve()
    raise SystemExit("history.py: cannot find the clean_eval package; set CLEAN_EVAL_HOME to the clean-eval checkout")


ROOT = clean_eval_home()
sys.path.insert(0, str(ROOT))
from clean_eval.adaptor.gateways.files import load_policy, policy_from_text  # noqa: E402
from clean_eval.adaptor.gateways.git_cli import Git  # noqa: E402
from clean_eval.domain.change import assess_change  # noqa: E402
from clean_eval.domain.classify import ignored  # noqa: E402
from clean_eval.domain.entities import Heatmap  # noqa: E402
from clean_eval.domain.errors import NoPythonSource  # noqa: E402
from clean_eval.domain.policy import BAND_NAMES, SCORE_KEYS  # noqa: E402
from clean_eval.domain.rating import rescore as rescore_module  # noqa: E402
from clean_eval.domain.scoring import band, band_floor, severity  # noqa: E402
from clean_eval.external.wiring import RevisionRaters, today  # noqa: E402

RULE_ID = re.compile(r"\[([A-Z]{1,4}-\d{2,3}|NEW)\]")  # pr-review tags findings as [CA-01] / [NEW]
MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")
MERGE_PR = re.compile(r"Merge pull request #(\d+)")
SQUASH_PR = re.compile(r"\(#(\d+)\)")  # GitHub squash subjects end with "(#123)"; take the last one
NOT_FOUND = re.compile(r"Could not resolve to a PullRequest|HTTP 404|Not Found", re.I)
GH_ERROR_RATE_LIMIT = 0.2  # above this share of failed fetches, rule metrics are flagged unreliable
SENSITIVITY_GROUPS = ("business_weights", "effects_mix", "entropy_weights", "unverified_mix")


def pr_number(subject):
    m = MERGE_PR.search(subject)
    if m:
        return int(m.group(1))
    found = SQUASH_PR.findall(subject)
    return int(found[-1]) if found else None


def default_branch(git):
    """origin/HEAD, else origin/main|master (CI checkouts don't set origin/HEAD), else HEAD."""
    ref = git.run("symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
    if ref:
        return ref
    for ref in ("origin/main", "origin/master", "main", "master"):
        if git.has_commit(ref):
            return ref
    return "HEAD"


def first_parent_units(git, branch, since, until):
    raw = git.run("log", "--first-parent", branch, "--since=%s" % since, "--until=%s" % until,
                  "--format=%H%x1f%P%x1f%an%x1f%ct%x1f%s")
    units = []
    for line in raw.splitlines():
        sha, parents, author, ts, subject = line.split("\x1f", 4)
        parents = parents.split()
        if not parents:
            continue
        units.append({"sha": sha, "base": parents[0], "author": author, "ts": int(ts), "subject": subject,
                      "pr": pr_number(subject), "files": git.diff_files(parents[0], sha)})
    units.sort(key=lambda u: u["ts"])
    return units


# ---------------------------------------------------------------- GitHub review data

class GhError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind  # "not_found" | "error"


def _loads_multi(text):
    """Parse gh output; `gh api --paginate` concatenates one JSON document per page."""
    dec, i, docs, text = json.JSONDecoder(), 0, [], text.strip()
    while i < len(text):
        doc, i = dec.raw_decode(text, i)
        docs.append(doc)
        while i < len(text) and text[i].isspace():
            i += 1
    if len(docs) == 1:
        return docs[0]
    if all(isinstance(d, list) for d in docs):
        return [x for d in docs for x in d]
    return docs


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()[:300]
        raise GhError("not_found" if NOT_FOUND.search(msg) else "error", msg)
    try:
        return _loads_multi(r.stdout)
    except json.JSONDecodeError as e:
        raise GhError("error", "unparseable gh output: %s" % e)


def is_reviewer(login, reviewer_logins=None):
    """Whose clean-eval markers count: the given logins, else any GitHub App bot (`name[bot]` logins can't be
    registered by users, so a commenter can't forge one)."""
    if not login:
        return False
    return login in reviewer_logins if reviewer_logins else login.endswith("[bot]")


def gh_review_data(pr, reviewer_logins=None):
    """Review signal for one PR, or {"error": kind, "message": ...} if it couldn't be fetched."""
    try:
        view = gh_json("pr", "view", str(pr), "--json", "reviews,comments,labels,additions,deletions,url")
        inline = gh_json("api", "--paginate", "repos/{owner}/{repo}/pulls/%s/comments" % pr)
    except GhError as e:
        return {"error": e.kind, "message": str(e)}
    posts = [((r.get("author") or {}).get("login"), r.get("body") or "") for r in view.get("reviews", [])]
    posts += [((c.get("author") or {}).get("login"), c.get("body") or "") for c in view.get("comments", [])]
    posts += [((c.get("user") or {}).get("login"), c.get("body") or "") for c in inline]
    marker, marker_authors, ignored_markers = {}, set(), 0
    for login, body in posts:
        for m in MARKER.finditer(body):
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
    return {
        "url": view.get("url"),
        "labels": [l.get("name") for l in view.get("labels", [])],
        "size": (view.get("additions") or 0) + (view.get("deletions") or 0),
        "review_comment_count": len([b for _, b in posts if b.strip()]),
        "rules_cited": sorted(set(tags) - {"NEW"}),
        "new_findings": tags.count("NEW"),
        "clean_eval_marker": marker or None,
        "untrusted_markers_ignored": ignored_markers,
    }


def require_gh_auth():
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("--with-gh needs an authenticated gh CLI (gh auth status failed): " + (r.stderr or r.stdout).strip()[:200])


# ---------------------------------------------------------------- metrics

def auc(pos, neg):
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def _aspect(u, key):
    """Highest score of one aspect among the rated modules a PR touched (0 when none)."""
    return max([m["scores"].get(key) or 0 for m in u["modules"] if m.get("rated")] or [0])


def calibration(prs):
    by_band = defaultdict(lambda: {"prs": 0, "defective_prs": 0})
    for u in prs:
        by_band[u["max_tier"]]["prs"] += 1
        by_band[u["max_tier"]]["defective_prs"] += bool(u["defects"])
    for b in by_band.values():
        b["escape_rate"] = round(b["defective_prs"] / b["prs"], 3) if b["prs"] else None
    bad, good = [u for u in prs if u["defects"]], [u for u in prs if not u["defects"]]
    aspect_auc, aspect_means = {}, {}
    for key in SCORE_KEYS:
        sp, sn = [_aspect(u, key) for u in bad], [_aspect(u, key) for u in good]
        aspect_auc[key] = auc(sp, sn)
        aspect_means[key] = {"defective": round(sum(sp) / len(sp), 1) if sp else None,
                             "clean": round(sum(sn) / len(sn), 1) if sn else None}
    bands = [b for b in BAND_NAMES if b in by_band]
    return {
        "auc_max_score": auc([u["max_score"] for u in bad], [u["max_score"] for u in good]),
        "auc_note": "0.5 = the overall score is no better than random at predicting escaped defects; 1.0 = perfect",
        "by_band": {b: by_band[b] for b in bands},
        "band_mix": {b: round(by_band[b]["prs"] / len(prs), 3) for b in bands} if prs else {},
        "aspect_auc": aspect_auc, "aspect_means": aspect_means,
    }


def _summarise(u, mods):
    u = dict(u, modules=mods)
    u["max_tier"] = max([m["band"] for m in mods] or ["Low"], key=severity)
    scores = [m["score"] for m in mods if m["score"] is not None] + [band_floor(m["band"]) for m in mods if m["score"] is None]
    u["max_score"] = max(scores, default=0)
    return u


def rescore(prs, policy):
    """Copy of `prs` with every rated module re-scored from its facts under `policy`."""
    out = []
    for u in prs:
        mods = []
        for m in u["modules"]:
            if m.get("rated") and m.get("facts"):
                s = rescore_module(m["facts"], policy)
                mods.append({**m, "scores": s, "score": s["overall"], "band": band(s["overall"])})
            else:
                mods.append({**m, "band": policy.unrated_band, "score": None})
        out.append(_summarise(u, mods))
    return out


def replay(history, policy):
    """Calibration of `policy` on an already-collected history (weights, mixes and thresholds; not module shape)."""
    cal = calibration(rescore(history["prs"], policy))
    return {k: cal[k] for k in ("auc_max_score", "by_band", "band_mix")}


def sensitivity(history, policy, step=0.1):
    """One-knob-at-a-time replays: the overall peak share and every weight of the four aspects, each ±step and -> 0."""
    rows = [{"change": "baseline", **replay(history, policy)}]
    for nv in (policy.overall_peak_share + step, policy.overall_peak_share - step):
        if 0 <= nv <= 1:
            rows.append({"change": "overall_peak_share %g→%g" % (policy.overall_peak_share, round(nv, 3)),
                         **replay(history, dataclasses.replace(policy, overall_peak_share=round(nv, 3)))})
    for group in SENSITIVITY_GROUPS:
        weights = getattr(policy, group)
        for key, w in weights.items():
            seen = {round(w, 3)}
            for label, nw in (("+%g" % step, w + step), ("-%g" % step, max(0.0, w - step)), ("=0", 0.0)):
                nw = round(nw, 3)
                if nw in seen or sum(weights.values()) - w + nw <= 0:
                    continue
                seen.add(nw)
                variant = dataclasses.replace(policy, **{group: {**weights, key: nw}})
                rows.append({"change": "%s.%s %g→%g (%s)" % (group, key, w, nw, label), **replay(history, variant)})
    return rows


# ---------------------------------------------------------------- collection

def _module_record(r):
    return {"module": r.module, "band": r.band, "score": r.score, "scores": r.scores, "rated": r.rated, "facts": r.facts}


def collect(repo, policy, since, until, branch=None, defect_window_days=30, with_gh=False, model_source=None,
            reviewer_logins=None):
    git = Git(repo)
    branch = branch or default_branch(git)
    fix_re = re.compile(r"(?i)\b(fix(es|ed)?|bug|hotfix|regression|patch|revert)\b")
    revert_re = re.compile(r"(?i)^revert\b")
    if with_gh:
        require_gh_auth()

    lookahead_until = (dt.date.fromisoformat(until) + dt.timedelta(days=defect_window_days)).isoformat()
    all_units = first_parent_units(git, branch, since, lookahead_until)
    until_ts = dt.datetime.fromisoformat(until + "T23:59:59+00:00").timestamp()
    in_period = [u for u in all_units if u["ts"] <= until_ts]

    # ---- the heat map at each PR's base (one rating per base; contents and analysis shared across bases)
    raters, heatmaps = RevisionRaters(repo), {}
    for u in in_period:
        if u["base"] not in heatmaps:
            try:
                heatmaps[u["base"]] = raters.at(u["base"], policy).execute(policy, today())
            except NoPythonSource:
                heatmaps[u["base"]] = Heatmap(repo="", ref=u["base"], sha=u["base"][:7], generated_at=today(), loc_total=0,
                                              modules=[], notes=[])
        risk = assess_change(heatmaps[u["base"]], u["files"], policy)
        u.update(max_tier=risk.max_band, max_score=risk.max_score, modules=[_module_record(r) for r in risk.modules])

    # ---- escaped defects: attribute each fix/revert to the most recent earlier PR sharing files
    window = defect_window_days * 86400
    by_sha = {u["sha"]: u for u in in_period}
    for u in in_period:
        u["defects"] = []
    for fix in all_units:
        is_revert = bool(revert_re.search(fix["subject"]))
        if not (is_revert or fix_re.search(fix["subject"])):
            continue
        fset = {f for f in fix["files"] if not ignored(f, policy)}
        candidates = [u for u in in_period
                      if u["sha"] != fix["sha"] and 0 < fix["ts"] - u["ts"] <= window and fset & set(u["files"])]
        if not candidates:
            continue
        strong = [u for u in candidates if u["sha"][:7] in fix["subject"] or (is_revert and u["subject"] in fix["subject"])]
        target = (strong or candidates)[-1]
        by_sha[target["sha"]]["defects"].append({
            "fix_sha": fix["sha"], "fix_pr": fix["pr"], "subject": fix["subject"],
            "kind": "revert" if is_revert else "fix", "confidence": "high" if strong else "heuristic",
            "days_after": round((fix["ts"] - target["ts"]) / 86400, 1),
            "overlap_files": sorted(fset & set(target["files"]))[:20],
        })

    rules = None
    if with_gh:
        for u in in_period:
            if u["pr"]:
                u["review"] = gh_review_data(u["pr"], reviewer_logins)
        fetched = [u for u in in_period if u.get("review") and "error" not in u["review"]]
        failed = [u["pr"] for u in in_period if u.get("review", {}).get("error") == "error"]
        not_found = [u["pr"] for u in in_period if u.get("review", {}).get("error") == "not_found"]
        attempted = len(fetched) + len(failed)
        error_rate = round(len(failed) / attempted, 3) if attempted else 0.0
        per_rule = defaultdict(lambda: {"prs_cited": 0, "cited_in_defective_prs": 0})
        for u in fetched:
            for r in u["review"]["rules_cited"]:
                per_rule[r]["prs_cited"] += 1
                per_rule[r]["cited_in_defective_prs"] += bool(u["defects"])
        rules = {"per_rule": dict(per_rule), "prs_reviewed": len(fetched),
                 "prs_fetch_failed": failed, "prs_not_found": not_found, "gh_error_rate": error_rate,
                 "reliable": error_rate <= GH_ERROR_RATE_LIMIT,
                 "prs_with_clean_eval_review": sum(1 for u in fetched if u["review"]["clean_eval_marker"]),
                 "new_findings": sum(u["review"]["new_findings"] for u in fetched),
                 "defective_prs_with_no_rule_cited": [u["pr"] for u in fetched if u["defects"] and not u["review"]["rules_cited"]]}
        if failed:
            print("warning: gh review fetch failed for %d/%d PRs (%d%%)%s" % (len(failed), attempted, round(error_rate * 100),
                  "" if rules["reliable"] else " - rule metrics unreliable"), file=sys.stderr)

    by_module = defaultdict(lambda: {"prs": 0, "defective_prs": 0, "scores": [], "bands": defaultdict(int)})
    for u in in_period:
        for m in u["modules"]:
            bm = by_module[m["module"]]
            bm["prs"] += 1
            bm["defective_prs"] += bool(u["defects"])
            if m["score"] is not None:
                bm["scores"].append(m["score"])
            bm["bands"][m["band"]] += 1
    modules_out = {name: {"prs": bm["prs"], "defective_prs": bm["defective_prs"], "escape_rate": round(bm["defective_prs"] / bm["prs"], 3),
                          "mean_score": round(sum(bm["scores"]) / len(bm["scores"]), 1) if bm["scores"] else None,
                          "bands_seen": dict(bm["bands"])}
                   for name, bm in sorted(by_module.items(), key=lambda kv: -kv[1]["defective_prs"])}

    defective = sum(1 for u in in_period if u["defects"])
    return {
        "period": {"since": since, "until": until, "branch": branch, "model_source": model_source,
                   "defect_window_days": defect_window_days, "policy_version": policy.version},
        "totals": {"prs": len(in_period), "defective_prs": defective,
                   "escape_rate": round(defective / len(in_period), 3) if in_period else None},
        "calibration": calibration(in_period),
        "modules": modules_out,
        "rules": rules,
        "prs": [{**{k: v for k, v in u.items() if k != "files"}, "file_count": len(u["files"])} for u in in_period],
    }


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--model", help="risk-model overrides (default: <repo>/.clean-eval/risk-model.json when present)")
    ap.add_argument("--since", help="YYYY-MM-DD (required unless --from-history)")
    ap.add_argument("--until", default=dt.date.today().isoformat(), help="YYYY-MM-DD (default today)")
    ap.add_argument("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
    ap.add_argument("--defect-window-days", type=int, default=30)
    ap.add_argument("--with-gh", action="store_true", help="fetch review comments via gh")
    ap.add_argument("--reviewer-login", action="append",
                    help="login whose clean-eval markers count (repeatable; default: any [bot] login)")
    ap.add_argument("--from-history", help="reuse a collected history JSON instead of walking git")
    ap.add_argument("--replay", metavar="OVERRIDES_JSON", help="calibration of candidate risk-model overrides on --from-history")
    ap.add_argument("--sensitivity", action="store_true", help="one-knob replays of the policy on --from-history")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    root = Git(a.repo).toplevel()
    policy, source = load_policy(root, a.model)
    if a.replay or a.sensitivity:
        if not a.from_history:
            ap.error("--replay/--sensitivity need --from-history")
        history = json.loads(Path(a.from_history).read_text())
        out = (replay(history, policy_from_text(Path(a.replay).read_text(), a.replay)) if a.replay
               else sensitivity(history, policy))
    else:
        if not a.since:
            ap.error("--since is required")
        out = collect(root, policy, a.since, a.until, a.branch, a.defect_window_days, a.with_gh, source, a.reviewer_login)

    text = json.dumps(out, indent=2, default=str)
    if a.out:
        Path(a.out).write_text(text)
        if isinstance(out, dict) and "totals" in out:
            print("wrote %s: %d PRs, %d with escaped defects" % (a.out, out["totals"]["prs"], out["totals"]["defective_prs"]),
                  file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
