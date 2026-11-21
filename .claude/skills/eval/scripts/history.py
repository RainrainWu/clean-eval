#!/usr/bin/env python3
"""Collect merged-PR history for a period and score how well the risk model and review rules did.

Each first-parent commit on the main branch is treated as one merged PR (merge or squash).
For every PR we record the risk of the modules it touched *as of its base commit* (so the score
is what the heat map would have said at review time), then look ahead `--defect-window-days`
for fix/revert commits that touch the same files — "escaped defects" (a heuristic; the skill
asks the agent to confirm them by reading diffs).

With --with-gh, PR review threads are fetched via the GitHub CLI and rule IDs cited in them
(e.g. CA-01, Q-02) plus the pr-review marker `<!-- clean-eval:pr-review ... -->` are extracted.

Output: JSON on stdout (or --out). Pure standard library + git (+ gh optionally).
"""
import argparse
import datetime as dt
import fnmatch
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

RULE_ID = re.compile(r"\b([A-Z]{2,4}-\d{2,3})\b")
MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")
PR_NUM = re.compile(r"(?:#|pull request #)(\d+)")
SIGNALS = ["churn", "fix_ratio", "test_gap", "size", "coupling", "criticality"]


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
        files = [f for f in git(repo, "diff", "--name-only", parents[0], sha).splitlines() if f]
        m = PR_NUM.search(subject)
        units.append({"sha": sha, "base": parents[0], "author": author, "ts": int(ts),
                      "subject": subject, "pr": int(m.group(1)) if m else None, "files": files})
    units.sort(key=lambda u: u["ts"])
    return units


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def gh_review_data(pr):
    view = gh_json("pr", "view", str(pr), "--json", "reviews,comments,labels,additions,deletions,url") or {}
    inline = gh_json("api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{pr}/comments") or []
    bodies = [r.get("body") or "" for r in view.get("reviews", [])]
    bodies += [c.get("body") or "" for c in view.get("comments", [])]
    bodies += [c.get("body") or "" for c in inline]
    text = "\n".join(bodies)
    marker = {}
    for m in MARKER.finditer(text):
        for kv in m.group(1).split():
            k, _, v = kv.partition("=")
            marker[k] = v
    return {
        "url": view.get("url"),
        "labels": [l.get("name") for l in view.get("labels", [])],
        "size": (view.get("additions") or 0) + (view.get("deletions") or 0),
        "review_comment_count": len([b for b in bodies if b.strip()]),
        "rules_cited": sorted(set(RULE_ID.findall(text))),
        "clean_eval_marker": marker or None,
    }


def auc(pos, neg):
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--model", help="default: <repo>/.clean-eval/risk-model.json, else the bundled default")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--until", default=dt.date.today().isoformat(), help="YYYY-MM-DD (default today)")
    ap.add_argument("--branch", help="default: origin/HEAD or current branch")
    ap.add_argument("--defect-window-days", type=int, default=30)
    ap.add_argument("--with-gh", action="store_true", help="fetch review comments via gh")
    ap.add_argument("--out")
    a = ap.parse_args()

    repo = risk.repo_root(a.repo)
    model_path = risk.resolve_model_path(repo, a.model)
    model = risk.load_model(model_path)
    branch = a.branch or default_branch(repo)
    fix_re = re.compile(model.get("fix_pattern", r"$^"))
    revert_re = re.compile(model.get("revert_pattern", r"(?i)^revert\b"))
    excluded = lambda f: any(fnmatch.fnmatch(f, p) for p in model.get("exclude", []))  # noqa: E731

    lookahead_until = (dt.date.fromisoformat(a.until) + dt.timedelta(days=a.defect_window_days)).isoformat()
    all_units = first_parent_units(repo, branch, a.since, lookahead_until)
    until_ts = dt.datetime.fromisoformat(a.until + "T23:59:59+00:00").timestamp()
    in_period = [u for u in all_units if u["ts"] <= until_ts]

    # ---- risk at each PR's base (cached per base)
    cache = {}
    for u in in_period:
        if u["base"] not in cache:
            cache[u["base"]] = risk.compute(repo, model, u["base"])
        c = risk.classify(repo, model, u["files"], result=cache[u["base"]])
        u["max_tier"], u["max_score"] = c["max_tier"], c["max_score"]
        u["modules"] = [{"module": m["module"], "tier": m["tier"], "score": m.get("score", 0),
                         "signals": m.get("signals", {})} for m in c["modules"]]

    # ---- escaped defects: attribute each fix/revert to the most recent earlier PR sharing files
    window = a.defect_window_days * 86400
    by_sha = {u["sha"]: u for u in in_period}
    for u in in_period:
        u["defects"] = []
    for fix in all_units:
        is_revert = bool(revert_re.search(fix["subject"]))
        if not (is_revert or fix_re.search(fix["subject"])):
            continue
        fset = {f for f in fix["files"] if not excluded(f)}
        candidates = [u for u in in_period
                      if u["sha"] != fix["sha"] and 0 < fix["ts"] - u["ts"] <= window and fset & set(u["files"])]
        if not candidates:
            continue
        strong = [u for u in candidates if u["sha"][:7] in fix["subject"] or (is_revert and u["subject"] in fix["subject"])]
        target = (strong or candidates)[-1]
        by_sha[target["sha"]]["defects"].append({
            "fix_sha": fix["sha"][:10], "fix_pr": fix["pr"], "subject": fix["subject"],
            "kind": "revert" if is_revert else "fix", "confidence": "high" if strong else "heuristic",
            "days_after": round((fix["ts"] - target["ts"]) / 86400, 1),
            "overlap_files": sorted(fset & set(target["files"]))[:20],
        })

    if a.with_gh:
        for u in in_period:
            if u["pr"]:
                u["review"] = gh_review_data(u["pr"])

    # ---- aggregates
    by_tier = defaultdict(lambda: {"prs": 0, "defective_prs": 0})
    by_module = defaultdict(lambda: {"prs": 0, "defective_prs": 0, "scores": [], "tiers": defaultdict(int)})
    for u in in_period:
        bad = bool(u["defects"])
        by_tier[u["max_tier"]]["prs"] += 1
        by_tier[u["max_tier"]]["defective_prs"] += bad
        for m in u["modules"]:
            bm = by_module[m["module"]]
            bm["prs"] += 1
            bm["defective_prs"] += bad
            bm["scores"].append(m["score"])
            bm["tiers"][m["tier"]] += 1
    for t in by_tier.values():
        t["escape_rate"] = round(t["defective_prs"] / t["prs"], 3) if t["prs"] else None
    modules_out = {}
    for name, bm in sorted(by_module.items(), key=lambda kv: -kv[1]["defective_prs"]):
        modules_out[name] = {"prs": bm["prs"], "defective_prs": bm["defective_prs"],
                             "escape_rate": round(bm["defective_prs"] / bm["prs"], 3),
                             "mean_score": round(sum(bm["scores"]) / len(bm["scores"]), 3),
                             "tiers_seen": dict(bm["tiers"])}

    pos = [u["max_score"] for u in in_period if u["defects"]]
    neg = [u["max_score"] for u in in_period if not u["defects"]]
    signal_auc, signal_means = {}, {}
    for s in SIGNALS:
        sp = [max([m["signals"].get(s, 0) for m in u["modules"]] or [0]) for u in in_period if u["defects"]]
        sn = [max([m["signals"].get(s, 0) for m in u["modules"]] or [0]) for u in in_period if not u["defects"]]
        signal_auc[s] = auc(sp, sn)
        signal_means[s] = {"defective": round(sum(sp) / len(sp), 3) if sp else None,
                           "clean": round(sum(sn) / len(sn), 3) if sn else None}

    rules = None
    if a.with_gh:
        rules = defaultdict(lambda: {"prs_cited": 0, "cited_in_defective_prs": 0})
        reviewed = [u for u in in_period if u.get("review")]
        for u in reviewed:
            for r in u["review"]["rules_cited"]:
                rules[r]["prs_cited"] += 1
                rules[r]["cited_in_defective_prs"] += bool(u["defects"])
        rules = {"per_rule": dict(rules),
                 "prs_reviewed": len(reviewed),
                 "prs_with_clean_eval_review": sum(1 for u in reviewed if u["review"]["clean_eval_marker"]),
                 "defective_prs_with_no_rule_cited": [u["pr"] for u in reviewed
                                                      if u["defects"] and not u["review"]["rules_cited"]]}

    out = {
        "period": {"since": a.since, "until": a.until, "branch": branch, "model_path": model_path,
                   "defect_window_days": a.defect_window_days, "model_version": model.get("version")},
        "totals": {"prs": len(in_period), "defective_prs": len(pos),
                   "escape_rate": round(len(pos) / len(in_period), 3) if in_period else None},
        "calibration": {
            "auc_max_score": auc(pos, neg),
            "auc_note": "0.5 = risk score no better than random at predicting escaped defects; 1.0 = perfect",
            "by_tier": {t: by_tier[t] for t in risk.TIER_ORDER if t in by_tier},
            "signal_auc": signal_auc, "signal_means": signal_means,
        },
        "modules": modules_out,
        "rules": rules,
        "prs": [{k: v for k, v in u.items() if k not in ("files",)} | {"file_count": len(u["files"])}
                for u in in_period],
    }
    text = json.dumps(out, indent=2, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
        print(f"wrote {a.out}: {len(in_period)} PRs, {len(pos)} with escaped defects", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
