#!/usr/bin/env python3
"""Collect merged-PR history for a period and score how well the risk model and review rules did.

Each first-parent commit on the main branch is treated as one merged PR (merge or squash).
For every PR we record the risk of the modules it touched *as of its base commit* (so the score
is what the heat map would have said at review time), then look ahead `--defect-window-days`
for fix/revert commits that touch the same files — "escaped defects" (a heuristic; the skill
asks the agent to confirm them by reading diffs).

With --with-gh, PR review threads are fetched via the GitHub CLI and rule tags cited in them
([CA-01], [Q-02], [NEW]) plus the pr-review marker `<!-- clean-eval:pr-review ... -->` are extracted.
Fetch failures are recorded per PR and summarised; rule metrics are flagged unreliable when too many fail.

Replays (--replay / --sensitivity) re-score the collected per-module signals under a candidate model,
so weight/threshold/criticality changes can be evaluated in milliseconds without touching git.

Output: JSON on stdout (or --out). Pure standard library + git (+ gh optionally).
"""
import argparse
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RISK_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "risk-heat-map", "scripts"))
sys.path.insert(0, RISK_DIR)
import risk  # noqa: E402

RULE_ID = re.compile(r"\[([A-Z]{1,4}-\d{2,3}|NEW)\]")  # pr-review tags findings as [CA-01] / [NEW]
MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")
MERGE_PR = re.compile(r"Merge pull request #(\d+)")
SQUASH_PR = re.compile(r"\(#(\d+)\)")  # GitHub squash subjects end with "(#123)"; take the last one
NOT_FOUND = re.compile(r"Could not resolve to a PullRequest|HTTP 404|Not Found", re.I)
SIGNALS = list(risk.SIGNALS)
GH_ERROR_RATE_LIMIT = 0.2  # above this share of failed fetches, rule metrics are flagged unreliable


def pr_number(subject):
    m = MERGE_PR.search(subject)
    if m:
        return int(m.group(1))
    found = SQUASH_PR.findall(subject)
    return int(found[-1]) if found else None


def git(repo, *a):
    return risk.git(repo, *a)


def default_branch(repo):
    """origin/HEAD, else origin/main|master (CI checkouts don't set origin/HEAD), else HEAD."""
    try:
        return git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").strip()
    except RuntimeError:
        pass
    for ref in ("origin/main", "origin/master", "main", "master"):
        try:
            git(repo, "rev-parse", "--verify", "--quiet", ref)
            return ref
        except RuntimeError:
            continue
    return "HEAD"


def first_parent_units(repo, branch, since, until):
    raw = git(repo, "log", "--first-parent", branch, f"--since={since}", f"--until={until}",
              "--format=%H%x1f%P%x1f%an%x1f%ct%x1f%s")
    units = []
    for line in raw.splitlines():
        sha, parents, author, ts, subject = line.split("\x1f", 4)
        parents = parents.split()
        if not parents:
            continue
        units.append({"sha": sha, "base": parents[0], "author": author, "ts": int(ts),
                      "subject": subject, "pr": pr_number(subject),
                      "files": risk.diff_files(repo, parents[0], sha)})
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
        raise GhError("error", f"unparseable gh output: {e}")


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
        inline = gh_json("api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{pr}/comments")
    except GhError as e:
        return {"error": e.kind, "message": str(e)}
    posts = [((r.get("author") or {}).get("login"), r.get("body") or "") for r in view.get("reviews", [])]
    posts += [((c.get("author") or {}).get("login"), c.get("body") or "") for c in view.get("comments", [])]
    posts += [((c.get("user") or {}).get("login"), c.get("body") or "") for c in inline]
    marker, marker_authors, ignored = {}, set(), 0
    for login, body in posts:
        for m in MARKER.finditer(body):
            if not is_reviewer(login, reviewer_logins):
                ignored += 1  # a marker from anyone else is not trusted
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
        "untrusted_markers_ignored": ignored,
    }


def require_gh_auth():
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("--with-gh needs an authenticated gh CLI (gh auth status failed): "
                         + (r.stderr or r.stdout).strip()[:200])


# ---------------------------------------------------------------- metrics

def auc(pos, neg):
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def calibration(prs):
    by_tier = defaultdict(lambda: {"prs": 0, "defective_prs": 0})
    for u in prs:
        by_tier[u["max_tier"]]["prs"] += 1
        by_tier[u["max_tier"]]["defective_prs"] += bool(u["defects"])
    for t in by_tier.values():
        t["escape_rate"] = round(t["defective_prs"] / t["prs"], 3) if t["prs"] else None
    pos = [u["max_score"] for u in prs if u["defects"]]
    neg = [u["max_score"] for u in prs if not u["defects"]]
    signal_auc, signal_means = {}, {}
    for s in SIGNALS:
        sp = [max([m["signals"].get(s, 0) for m in u["modules"]] or [0]) for u in prs if u["defects"]]
        sn = [max([m["signals"].get(s, 0) for m in u["modules"]] or [0]) for u in prs if not u["defects"]]
        signal_auc[s] = auc(sp, sn)
        signal_means[s] = {"defective": round(sum(sp) / len(sp), 3) if sp else None,
                           "clean": round(sum(sn) / len(sn), 3) if sn else None}
    tiers = [t for t in risk.TIER_ORDER if t in by_tier]
    return {
        "auc_max_score": auc(pos, neg),
        "auc_note": "0.5 = risk score no better than random at predicting escaped defects; 1.0 = perfect",
        "by_tier": {t: by_tier[t] for t in tiers},
        "tier_mix": {t: round(by_tier[t]["prs"] / len(prs), 3) for t in tiers} if prs else {},
        "signal_auc": signal_auc, "signal_means": signal_means,
    }


def rescore(prs, model):
    """Copy of `prs` with every touched module re-scored under `model` (signals are reused)."""
    new_tier = model.get("new_module_tier", "Medium")
    out = []
    for u in prs:
        v = dict(u)
        mods = []
        for m in u["modules"]:
            if m.get("new_module") or not m.get("signals"):
                mods.append({**m, "tier": new_tier, "score": 0.0})
            else:
                s = risk.score_module(m["module"], m["signals"], model)
                mods.append({**m, "tier": s["tier"], "score": s["score"], "signals": s["signals"]})
        v["modules"] = mods
        v["max_tier"] = max([m["tier"] for m in mods] or ["Low"], key=risk.TIER_ORDER.index)
        v["max_score"] = max([m["score"] for m in mods] or [0])
        out.append(v)
    return out


def replay(history, model):
    """Calibration of `model` on an already-collected history (weights/thresholds/criticality only)."""
    cal = calibration(rescore(history["prs"], model))
    return {k: cal[k] for k in ("auc_max_score", "by_tier", "tier_mix")}


def _variant(model, path, value):
    m = copy.deepcopy(model)
    node = m
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    return m


def sensitivity(history, model, step=0.1, th_step=0.05):
    """One-knob-at-a-time replays: each weight ±step and → 0, each threshold ±th_step."""
    base = replay(history, model)
    rows = [{"change": "baseline", **base}]
    for axis in ("likelihood_weights", "impact_weights"):
        for sig, w in model[axis].items():
            seen = {round(w, 3)}
            for label, nw in ((f"+{step}", w + step), (f"-{step}", max(0.0, w - step)), ("=0", 0.0)):
                if round(nw, 3) in seen or sum(model[axis].values()) - w + nw <= 0:
                    continue
                seen.add(round(nw, 3))
                r = replay(history, _variant(model, (axis, sig), round(nw, 3)))
                rows.append({"change": f"{axis}.{sig} {w}→{round(nw, 3)} ({label})", **r})
    th = model.get("level_thresholds", {"medium": 0.33, "high": 0.66})
    for k in ("medium", "high"):
        for d in (th_step, -th_step):
            nv = round(th[k] + d, 3)
            if not 0 < nv < 1 or (k == "medium" and nv >= th["high"]) or (k == "high" and nv <= th["medium"]):
                continue
            r = replay(history, _variant(model, ("level_thresholds", k), nv))
            rows.append({"change": f"level_thresholds.{k} {th[k]}→{nv}", **r})
    return rows


# ---------------------------------------------------------------- collection

def collect(repo, model, since, until, branch=None, defect_window_days=30, with_gh=False, model_path=None,
            reviewer_logins=None):
    branch = branch or default_branch(repo)
    fix_re = re.compile(model.get("fix_pattern", r"$^"))
    revert_re = re.compile(model.get("revert_pattern", r"(?i)^revert\b"))
    noise = risk.ModuleMap(model, [])  # exclude/include matching only
    if with_gh:
        require_gh_auth()

    lookahead_until = (dt.date.fromisoformat(until) + dt.timedelta(days=defect_window_days)).isoformat()
    all_units = first_parent_units(repo, branch, since, lookahead_until)
    until_ts = dt.datetime.fromisoformat(until + "T23:59:59+00:00").timestamp()
    in_period = [u for u in all_units if u["ts"] <= until_ts]

    # ---- risk at each PR's base (cached per base)
    cache = {}
    for u in in_period:
        if u["base"] not in cache:
            cache[u["base"]] = risk.compute(repo, model, u["base"])
        c = risk.classify(repo, model, u["files"], result=cache[u["base"]])
        u["max_tier"], u["max_score"] = c["max_tier"], c["max_score"]
        u["modules"] = [{"module": m["module"], "tier": m["tier"], "score": m.get("score", 0),
                         "signals": m.get("signals", {}), "new_module": bool(m.get("new_module"))}
                        for m in c["modules"]]

    # ---- escaped defects: attribute each fix/revert to the most recent earlier PR sharing files
    window = defect_window_days * 86400
    by_sha = {u["sha"]: u for u in in_period}
    for u in in_period:
        u["defects"] = []
    for fix in all_units:
        is_revert = bool(revert_re.search(fix["subject"]))
        if not (is_revert or fix_re.search(fix["subject"])):
            continue
        fset = {f for f in fix["files"] if not noise.excluded(f)}
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
        rules = {"per_rule": dict(per_rule),
                 "prs_reviewed": len(fetched),
                 "prs_fetch_failed": failed, "prs_not_found": not_found,
                 "gh_error_rate": error_rate,
                 "reliable": error_rate <= GH_ERROR_RATE_LIMIT,
                 "prs_with_clean_eval_review": sum(1 for u in fetched if u["review"]["clean_eval_marker"]),
                 "new_findings": sum(u["review"]["new_findings"] for u in fetched),
                 "defective_prs_with_no_rule_cited": [u["pr"] for u in fetched
                                                      if u["defects"] and not u["review"]["rules_cited"]]}
        if failed:
            print(f"warning: gh review fetch failed for {len(failed)}/{attempted} PRs "
                  f"({error_rate:.0%}){'' if rules['reliable'] else ' — rule metrics unreliable'}", file=sys.stderr)

    by_module = defaultdict(lambda: {"prs": 0, "defective_prs": 0, "scores": [], "tiers": defaultdict(int)})
    for u in in_period:
        for m in u["modules"]:
            bm = by_module[m["module"]]
            bm["prs"] += 1
            bm["defective_prs"] += bool(u["defects"])
            bm["scores"].append(m["score"])
            bm["tiers"][m["tier"]] += 1
    modules_out = {name: {"prs": bm["prs"], "defective_prs": bm["defective_prs"],
                          "escape_rate": round(bm["defective_prs"] / bm["prs"], 3),
                          "mean_score": round(sum(bm["scores"]) / len(bm["scores"]), 3),
                          "tiers_seen": dict(bm["tiers"])}
                   for name, bm in sorted(by_module.items(), key=lambda kv: -kv[1]["defective_prs"])}

    defective = sum(1 for u in in_period if u["defects"])
    return {
        "period": {"since": since, "until": until, "branch": branch, "model_path": model_path,
                   "defect_window_days": defect_window_days, "model_version": model.get("version")},
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
    ap.add_argument("--model", help="default: <repo>/.clean-eval/risk-model.json, else the bundled default")
    ap.add_argument("--since", help="YYYY-MM-DD (required unless --from-history)")
    ap.add_argument("--until", default=dt.date.today().isoformat(), help="YYYY-MM-DD (default today)")
    ap.add_argument("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
    ap.add_argument("--defect-window-days", type=int, default=30)
    ap.add_argument("--with-gh", action="store_true", help="fetch review comments via gh")
    ap.add_argument("--reviewer-login", action="append",
                    help="login whose clean-eval markers count (repeatable; default: any [bot] login)")
    ap.add_argument("--from-history", help="reuse a collected history JSON instead of walking git")
    ap.add_argument("--replay", metavar="MODEL_JSON", help="calibration of a candidate model on --from-history")
    ap.add_argument("--sensitivity", action="store_true", help="one-knob replays of the model on --from-history")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    repo = risk.repo_root(a.repo)
    model_path = risk.resolve_model_path(repo, a.model)
    model = risk.load_model(model_path)

    if a.replay or a.sensitivity:
        if not a.from_history:
            ap.error("--replay/--sensitivity need --from-history")
        history = risk.read_json(a.from_history)
        out = replay(history, risk.load_model(a.replay)) if a.replay else sensitivity(history, model)
    else:
        if not a.since:
            ap.error("--since is required")
        out = collect(repo, model, a.since, a.until, a.branch, a.defect_window_days, a.with_gh, model_path,
                      a.reviewer_login)

    text = json.dumps(out, indent=2, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        if isinstance(out, dict) and "totals" in out:
            print(f"wrote {a.out}: {out['totals']['prs']} PRs, {out['totals']['defective_prs']} with escaped defects",
                  file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
