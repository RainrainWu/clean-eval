#!/usr/bin/env python3
"""Precompute everything the CI agent needs, so it runs with no shell, no network and no token.

  prepare.py pr-review --pr N --ctx DIR      PR metadata, the diff (one patch per file), per-file history, the risk the
                                             change carries (from the heat map of its base) and the base's config
  prepare.py eval --since D --until D --ctx DIR [--with-gh]
                                             PR history + escaped defects, one-knob sensitivity replays, and patches
                                             for the defects to verify

Config for a PR review always comes from the PR's *base* commit (or the defaults), so a PR can't relax the rules or the
risk model it is judged by. Prints a small context JSON on stdout.
"""
import argparse
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, ".claude", "skills", "eval", "scripts"))
from clean_eval.adaptor.gateways.files import REPO_POLICY, load_policy, policy_from_text  # noqa: E402
from clean_eval.adaptor.gateways.git_cli import Git  # noqa: E402
from clean_eval.domain.errors import HeatmapError, InvalidPolicy  # noqa: E402
from clean_eval.domain.policy import Policy  # noqa: E402
from clean_eval.external import wiring  # noqa: E402
import history  # noqa: E402

DEFAULT_RULES = os.path.join(ROOT, ".claude", "skills", "pr-review", "review-rules.md")
REPO_RULES = os.path.join(".clean-eval", "review-rules.md")
MAX_HISTORY_FILES = 60
MAX_DEFECT_PATCHES = 30
MAX_PATCH_CHARS = 200_000


def write(path, text):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)


def write_json(path, obj):
    write(path, json.dumps(obj, indent=2, default=str))


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("gh %s failed: %s" % (" ".join(args[:2]), (r.stderr or r.stdout).strip()[:300]))
    return json.loads(r.stdout)


def blob_hash(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:8]  # same as `git hash-object`


def ensure_commit(git, sha, refspec):
    if not git.has_commit(sha):
        git.run("fetch", "--quiet", "origin", refspec, check=False)
    if not git.has_commit(sha):
        raise SystemExit("commit %s is not available locally (checkout with fetch-depth: 0)" % sha)


def policy_at(git, base):
    """(policy, source, overrides text, note) from the base commit's .clean-eval/risk-model.json, else the defaults.
    An invalid file at the base falls back to the defaults with a note, rather than failing every review."""
    text = git.show(base, REPO_POLICY)
    if text is None:
        return Policy(), "clean-eval default", "{}", None
    try:
        return policy_from_text(text, REPO_POLICY), "%s@%s" % (REPO_POLICY, base[:8]), text, None
    except InvalidPolicy as e:
        return Policy(), "clean-eval default", "{}", "ignored %s at the base: %s" % (REPO_POLICY, e)


def truncate(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[... truncated: %d more characters ...]\n" % (len(text) - limit), True


def split_patch(full):
    """Split a `git diff` into one chunk per file (each starts with 'diff --git ')."""
    chunks, cur = [], []
    for line in full.splitlines(keepends=True):
        if line.startswith("diff --git ") and cur:
            chunks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def write_split_diff(git, base, head, files, file_modules, out_dir):
    """One patch per changed file plus diff/index.json, so a reviewer can read every hunk in full
    (a single combined diff is easily longer than one file read)."""
    os.makedirs(out_dir, exist_ok=True)
    chunks = split_patch(git.run("diff", "-M", base, head))
    if len(chunks) != len(files):  # unexpected shape (e.g. renames): fall back to per-path diffs
        chunks = [git.run("--literal-pathspecs", "diff", base, head, "--", f) for f in files]
    index = []
    for i, (f, chunk) in enumerate(zip(files, chunks), 1):
        text, cut = truncate(chunk, MAX_PATCH_CHARS)
        name = "%03d.patch" % i
        write(os.path.join(out_dir, name), text)
        added = sum(1 for l in chunk.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in chunk.splitlines() if l.startswith("-") and not l.startswith("---"))
        index.append({"file": f, "module": file_modules.get(f), "patch": "diff/" + name,
                      "patch_lines": text.count("\n"), "added": added, "removed": removed, "truncated": cut})
    write_json(os.path.join(out_dir, "index.json"), index)
    return index


def prepare_pr_review(repo, pr, ctx):
    git = Git(repo)
    info = gh_json("pr", "view", str(pr), "--json",
                   "number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,isCrossRepository")
    head = info["headRefOid"]
    ensure_commit(git, head, "pull/%d/head" % pr)
    ensure_commit(git, info["baseRefOid"], info["baseRefOid"])
    base = git.merge_base(info["baseRefOid"], head)

    policy, model_src, model_text, model_note = policy_at(git, base)
    write(os.path.join(ctx, "risk-model.json"), model_text)
    rules = git.show(base, REPO_RULES)
    rules_src = "%s@%s" % (REPO_RULES, base[:8]) if rules is not None else "clean-eval default"
    rules = rules if rules is not None else Path(DEFAULT_RULES).read_text()
    write(os.path.join(ctx, "review-rules.md"), rules)

    heatmap, risk = wiring.assess_change(repo, base, head, policy)
    files = list(risk.files)
    context = {"pr": pr, "base": base, "head": head, "max_tier": risk.max_band, "max_score": risk.max_score,
               "model_source": model_src, "model_version": policy.version,
               "rules_source": rules_src, "rules_version": blob_hash(rules), "files_changed": len(files)}
    modules = [{"module": m.module, "band": m.band, "score": m.score, "scores": m.scores, "rated": m.rated,
                "evidence": m.evidence} for m in risk.modules]
    notes = heatmap.notes + ([model_note] if model_note else [])
    write_json(os.path.join(ctx, "risk.json"), {**context, "modules": modules, "files": risk.files,
                                                "ignored": risk.ignored, "notes": notes})

    pr_meta = {k: info.get(k) for k in ("number", "title", "body", "url", "baseRefName", "headRefName", "isCrossRepository")}
    pr_meta["author"] = (info.get("author") or {}).get("login")
    pr_meta["files"] = files
    write_json(os.path.join(ctx, "pr.json"), pr_meta)

    index = write_split_diff(git, base, head, files, risk.files, os.path.join(ctx, "diff"))
    context["diff_patches"] = len(index)
    context["diff_truncated"] = any(e["truncated"] for e in index)

    lines = []
    for f in files[:MAX_HISTORY_FILES]:
        log = git.run("--literal-pathspecs", "log", "--format=%h %ad %an: %s", "--date=short", "-n", "10", base, "--", f).strip()
        lines += ["## " + f, log or "(no history before this PR)", ""]
    if len(files) > MAX_HISTORY_FILES:
        lines.append("[history omitted for %d more files]" % (len(files) - MAX_HISTORY_FILES))
    write(os.path.join(ctx, "file-history.txt"), "\n".join(lines))

    write_json(os.path.join(ctx, "context.json"), context)
    return context


def prepare_eval(repo, since, until, ctx, with_gh, branch=None, defect_window_days=30):
    git = Git(repo)
    policy, model_source = load_policy(repo)
    model_path = os.path.join(repo, REPO_POLICY)
    model_existed = os.path.isfile(model_path)
    write(os.path.join(ctx, "risk-model.orig.json"), Path(model_path).read_text() if model_existed else "{}")
    write_json(os.path.join(ctx, "risk-model.defaults.json"), dataclasses.asdict(Policy()))
    # Snapshot .clean-eval/ so finalize can restore anything it doesn't validate.
    local_dir = os.path.join(repo, ".clean-eval")
    if os.path.isdir(local_dir):
        shutil.copytree(local_dir, os.path.join(ctx, "clean-eval.orig"), symlinks=True)
    rules_path = os.path.join(repo, REPO_RULES)
    shutil.copyfile(rules_path if os.path.exists(rules_path) else DEFAULT_RULES, os.path.join(ctx, "review-rules.orig.md"))

    hist = history.collect(repo, policy, since, until, branch, defect_window_days, with_gh, model_source)
    write_json(os.path.join(ctx, "eval-history.json"), hist)
    write_json(os.path.join(ctx, "sensitivity.json"), history.sensitivity(hist, policy))

    # Patches for the defects most worth verifying: highest bands first, then Low-band misses, then Moderate.
    order = {"Critical": 0, "High": 1, "Low": 2, "Moderate": 3}
    links = [(u, d) for u in hist["prs"] for d in u["defects"]]
    links.sort(key=lambda ud: (order.get(ud[0]["max_tier"], 9), ud[1]["confidence"] != "high"))
    index = []
    for i, (u, d) in enumerate(links[:MAX_DEFECT_PATCHES], 1):
        stem = "%02d-pr%s" % (i, u["pr"] or u["sha"][:8])
        for kind, sha in (("pr", u["sha"]), ("fix", d["fix_sha"])):
            patch, _ = truncate(git.run("--literal-pathspecs", "show", "--stat", "-p", "--format=%H%n%an %ad%n%s%n%n%b",
                                        sha, "--", *d["overlap_files"]), MAX_PATCH_CHARS)
            write(os.path.join(ctx, "defects", "%s-%s.patch" % (stem, kind)), patch)
        index.append({"file_stem": stem, "pr": u["pr"], "pr_sha": u["sha"], "pr_band": u["max_tier"],
                      "fix_sha": d["fix_sha"], "fix_subject": d["subject"], "kind": d["kind"],
                      "confidence": d["confidence"], "overlap_files": d["overlap_files"]})
    write_json(os.path.join(ctx, "defects", "index.json"), index)

    context = {"since": since, "until": until, "prs": hist["totals"]["prs"],
               "defective_prs": hist["totals"]["defective_prs"], "auc": hist["calibration"]["auc_max_score"],
               "model_source": model_source, "model_existed": model_existed, "rules_existed": os.path.exists(rules_path),
               "with_gh": with_gh, "rules_reliable": (hist["rules"] or {}).get("reliable"),
               "defect_patches": len(index), "defect_links": len(links)}
    write_json(os.path.join(ctx, "context.json"), context)
    return context


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pr-review")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--ctx", required=True)
    e = sub.add_parser("eval")
    e.add_argument("--since", required=True)
    e.add_argument("--until", required=True)
    e.add_argument("--ctx", required=True)
    e.add_argument("--with-gh", action="store_true")
    e.add_argument("--branch")
    a = ap.parse_args(argv)
    try:
        repo = Git(a.repo).toplevel()
        os.makedirs(a.ctx, exist_ok=True)
        out = prepare_pr_review(repo, a.pr, a.ctx) if a.cmd == "pr-review" else prepare_eval(repo, a.since, a.until, a.ctx, a.with_gh, a.branch)
    except HeatmapError as e:
        raise SystemExit("prepare: %s" % e)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
