#!/usr/bin/env python3
"""Precompute everything the CI agent needs, so it runs with no shell, no network and no token.

  prepare.py pr-review --pr N --ctx DIR      PR metadata, full diff, per-file history, risk at the
                                             base commit, and the base commit's config
  prepare.py eval --since D --until D --ctx DIR [--with-gh]
                                             PR history + escaped defects, one-knob sensitivity
                                             replays, and patches for the defects to verify

Config for a PR review always comes from the PR's *base* commit (or the bundled defaults), so a PR
can't relax the rules or risk model it is judged by. Prints a small context JSON on stdout.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.join(os.path.dirname(HERE), ".claude", "skills")
sys.path.insert(0, os.path.join(SKILLS, "risk-heat-map", "scripts"))
sys.path.insert(0, os.path.join(SKILLS, "eval", "scripts"))
import risk  # noqa: E402
import history  # noqa: E402

DEFAULT_RULES = os.path.join(SKILLS, "pr-review", "review-rules.md")
REPO_RULES = os.path.join(".clean-eval", "review-rules.md")
MAX_HISTORY_FILES = 60
MAX_DEFECT_PATCHES = 30
MAX_PATCH_CHARS = 200_000


def gh_json(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"gh {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return json.loads(r.stdout)


def blob_hash(text):
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:8]  # same as `git hash-object`


def has_commit(repo, sha):
    return subprocess.run(["git", "-C", repo, "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def ensure_commit(repo, sha, refspec):
    if not has_commit(repo, sha):
        risk.git(repo, "fetch", "--quiet", "origin", refspec)
    if not has_commit(repo, sha):
        raise SystemExit(f"commit {sha} is not available locally (checkout with fetch-depth: 0)")


def file_at(repo, rev, relpath, default, dest):
    """Copy <rev>:<relpath> to dest, else the bundled default. Returns where it came from."""
    try:
        content = risk.git(repo, "show", f"{rev}:{relpath}")
        source = f"{relpath}@{rev[:8]}"
    except RuntimeError:
        content = risk.read_text(default)
        source = "clean-eval default"
    with open(dest, "w") as f:
        f.write(content)
    return source, content


def truncate(text, limit):
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n\n[... truncated: {len(text) - limit} more characters ...]\n", True


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


def write_split_diff(repo, base, head, files, file_modules, out_dir):
    """One patch per changed file plus diff/index.json, so a reviewer can read every hunk in full
    (a single combined diff is easily longer than one file read)."""
    os.makedirs(out_dir, exist_ok=True)
    chunks = split_patch(risk.git(repo, "diff", "-M", base, head))
    if len(chunks) != len(files):  # unexpected shape: fall back to per-path diffs
        chunks = [risk.git(repo, "--literal-pathspecs", "diff", base, head, "--", f) for f in files]
    index = []
    for i, (f, chunk) in enumerate(zip(files, chunks), 1):
        text, cut = truncate(chunk, MAX_PATCH_CHARS)
        name = f"{i:03d}.patch"
        risk.write_text(os.path.join(out_dir, name), text)
        added = sum(1 for l in chunk.splitlines() if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in chunk.splitlines() if l.startswith("-") and not l.startswith("---"))
        index.append({"file": f, "module": file_modules.get(f), "patch": f"diff/{name}",
                      "patch_lines": text.count("\n"), "added": added, "removed": removed, "truncated": cut})
    risk.write_json(os.path.join(out_dir, "index.json"), index, indent=2)
    return index


def prepare_pr_review(repo, pr, ctx):
    info = gh_json("pr", "view", str(pr), "--json",
                   "number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,isCrossRepository")
    head = info["headRefOid"]
    ensure_commit(repo, head, f"pull/{pr}/head")
    ensure_commit(repo, info["baseRefOid"], info["baseRefOid"])
    base = risk.git(repo, "merge-base", info["baseRefOid"], head).strip()

    model_src, _ = file_at(repo, base, risk.REPO_MODEL, risk.DEFAULT_MODEL, os.path.join(ctx, "risk-model.json"))
    rules_src, rules = file_at(repo, base, REPO_RULES, DEFAULT_RULES, os.path.join(ctx, "review-rules.md"))
    model = risk.load_model(os.path.join(ctx, "risk-model.json"))

    files = risk.diff_files(repo, base, head)
    cls = risk.classify(repo, model, files, rev=base)
    context = {"pr": pr, "base": base, "head": head, "max_tier": cls["max_tier"], "max_score": cls["max_score"],
               "model_source": model_src, "model_version": model.get("version"),
               "rules_source": rules_src, "rules_version": blob_hash(rules), "files_changed": len(files)}
    risk.write_json(os.path.join(ctx, "risk.json"), {**cls, **context}, indent=2)

    pr_meta = {k: info.get(k) for k in ("number", "title", "body", "url", "baseRefName", "headRefName", "isCrossRepository")}
    pr_meta["author"] = (info.get("author") or {}).get("login")
    pr_meta["files"] = files
    risk.write_json(os.path.join(ctx, "pr.json"), pr_meta, indent=2)

    index = write_split_diff(repo, base, head, files, cls["files"], os.path.join(ctx, "diff"))
    context["diff_patches"] = len(index)
    context["diff_truncated"] = any(e["truncated"] for e in index)

    lines = []
    for f in files[:MAX_HISTORY_FILES]:
        log = risk.git(repo, "--literal-pathspecs", "log", "--format=%h %ad %an: %s", "--date=short",
                       "-n", "10", base, "--", f).strip()
        lines += [f"## {f}", log or "(no history before this PR)", ""]
    if len(files) > MAX_HISTORY_FILES:
        lines.append(f"[history omitted for {len(files) - MAX_HISTORY_FILES} more files]")
    risk.write_text(os.path.join(ctx, "file-history.txt"), "\n".join(lines))

    risk.write_json(os.path.join(ctx, "context.json"), context, indent=2)
    return context


def prepare_eval(repo, since, until, ctx, with_gh, branch=None, defect_window_days=30):
    model_path = risk.resolve_model_path(repo)
    model = risk.load_model(model_path)
    shutil.copyfile(model_path, os.path.join(ctx, "risk-model.orig.json"))
    # Snapshot .clean-eval/ so finalize can restore anything it doesn't validate.
    local_dir = os.path.join(repo, ".clean-eval")
    if os.path.isdir(local_dir):
        shutil.copytree(local_dir, os.path.join(ctx, "clean-eval.orig"), symlinks=True)
    rules_path = os.path.join(repo, REPO_RULES)
    shutil.copyfile(rules_path if os.path.exists(rules_path) else DEFAULT_RULES,
                    os.path.join(ctx, "review-rules.orig.md"))

    hist = history.collect(repo, model, since, until, branch, defect_window_days, with_gh, model_path)
    risk.write_json(os.path.join(ctx, "eval-history.json"), hist, indent=2, default=str)
    risk.write_json(os.path.join(ctx, "sensitivity.json"), history.sensitivity(hist, model), indent=2)

    # Patches for the defects most worth verifying: highest-tier PRs first, then Low-tier misses.
    order = {"Critical": 0, "High": 1, "Low": 2, "Medium": 3}
    links = [(u, d) for u in hist["prs"] for d in u["defects"]]
    links.sort(key=lambda ud: (order.get(ud[0]["max_tier"], 9), ud[1]["confidence"] != "high"))
    os.makedirs(os.path.join(ctx, "defects"), exist_ok=True)
    index = []
    for i, (u, d) in enumerate(links[:MAX_DEFECT_PATCHES], 1):
        stem = f"{i:02d}-pr{u['pr'] or u['sha'][:8]}"
        for kind, sha in (("pr", u["sha"]), ("fix", d["fix_sha"])):
            patch, _ = truncate(risk.git(repo, "--literal-pathspecs", "show", "--stat", "-p", "--format=%H%n%an %ad%n%s%n%n%b",
                                         sha, "--", *d["overlap_files"]), MAX_PATCH_CHARS)
            risk.write_text(os.path.join(ctx, "defects", f"{stem}-{kind}.patch"), patch)
        index.append({"file_stem": stem, "pr": u["pr"], "pr_sha": u["sha"], "pr_tier": u["max_tier"],
                      "fix_sha": d["fix_sha"], "fix_subject": d["subject"], "kind": d["kind"],
                      "confidence": d["confidence"], "overlap_files": d["overlap_files"]})
    risk.write_json(os.path.join(ctx, "defects", "index.json"), index, indent=2)

    context = {"since": since, "until": until, "prs": hist["totals"]["prs"],
               "defective_prs": hist["totals"]["defective_prs"], "auc": hist["calibration"]["auc_max_score"],
               "model_source": os.path.relpath(model_path, repo) if model_path.startswith(repo) else "clean-eval default",
               "model_existed": os.path.exists(os.path.join(repo, risk.REPO_MODEL)),
               "rules_existed": os.path.exists(rules_path),
               "with_gh": with_gh, "rules_reliable": (hist["rules"] or {}).get("reliable"),
               "defect_patches": len(index), "defect_links": len(links)}
    risk.write_json(os.path.join(ctx, "context.json"), context, indent=2)
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
    repo = risk.repo_root(a.repo)
    os.makedirs(a.ctx, exist_ok=True)
    if a.cmd == "pr-review":
        out = prepare_pr_review(repo, a.pr, a.ctx)
    else:
        out = prepare_eval(repo, a.since, a.until, a.ctx, a.with_gh, a.branch)
    print(json.dumps(out))


if __name__ == "__main__":
    main()
