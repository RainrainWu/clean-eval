#!/usr/bin/env python3
"""clean-eval risk model: module-level risk heat map from git history and source tree.

Pure standard library. Shared by the risk-heat-map, pr-review and eval skills.

Subcommands
  heatmap   compute the heat map; print markdown (default) or JSON; --write-readme updates README
  classify  map files to modules and report each touched module's risk at a revision

Model (all knobs live in risk-model.json):
  likelihood = weighted mean of  churn (log-normalised commits in window),
                                 fix_ratio (fix commits / (commits + prior)),
                                 test_gap (1 - test_loc / (src_loc * test_ratio_target), floored at 0)
  impact     = weighted mean of  size (log-normalised source LOC),
                                 coupling (commits that also touch other modules / (commits + prior)),
                                 criticality (human-assigned 0..1, default criticality_default)
  Each axis is bucketed Low/Medium/High by level_thresholds; the cell of the 3x3 grid gives the tier.
"""
import argparse
import datetime as dt
import fnmatch
import json
import math
import os
import re
import subprocess
import sys
from collections import defaultdict

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MODEL = os.path.join(SKILL_DIR, "risk-model.json")
REPO_MODEL = os.path.join(".clean-eval", "risk-model.json")  # per-repo override, relative to repo root
START_MARK = "<!-- risk-heat-map:start -->"
END_MARK = "<!-- risk-heat-map:end -->"
SECTION_HEADING = "## Risk Heat Map"

LEVELS = ["Low", "Medium", "High"]
TIER_ORDER = ["Low", "Medium", "High", "Critical"]
TIER_EMOJI = {"Critical": "🟥", "High": "🟧", "Medium": "🟨", "Low": "🟩"}
# (likelihood, impact) -> tier
TIER_GRID = {
    ("High", "High"): "Critical",
    ("High", "Medium"): "High", ("Medium", "High"): "High",
    ("Medium", "Medium"): "Medium", ("High", "Low"): "Medium", ("Low", "High"): "Medium",
}


# ---------------------------------------------------------------- git helpers

def git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def repo_root(path):
    return git(path, "rev-parse", "--show-toplevel").strip()


def resolve_model_path(repo, path=None):
    """--model > <repo>/.clean-eval/risk-model.json > bundled default."""
    if path:
        return path
    local = os.path.join(repo, REPO_MODEL)
    return local if os.path.exists(local) else DEFAULT_MODEL


def load_model(path=None):
    with open(path or DEFAULT_MODEL) as f:
        return json.load(f)


def line_counts(repo, rev=None):
    """{path: lines} for tracked text files at rev (or the working tree)."""
    args = ["grep", "-I", "-c", ""]
    if rev:
        args.append(rev)
    args.append("--")
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if r.returncode not in (0, 1):  # 1 == no matches
        raise RuntimeError(r.stderr.strip())
    out = {}
    prefix = f"{rev}:" if rev else ""
    for line in r.stdout.splitlines():
        if prefix and line.startswith(prefix):
            line = line[len(prefix):]
        path, _, n = line.rpartition(":")
        if path and n.isdigit():
            out[path] = int(n)
    return out


def commits_in_window(repo, rev, since, until):
    """List of dicts {sha, author, ts, subject, files} reachable from rev in [since, until]."""
    raw = git(repo, "log", rev or "HEAD", "--no-merges",
              f"--since={since.isoformat()}", f"--until={until.isoformat()}",
              "--format=%x1e%H%x1f%an%x1f%ct%x1f%s", "--name-only")
    commits = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        head, *files = chunk.split("\n")
        sha, author, ts, subject = head.split("\x1f", 3)
        commits.append({"sha": sha, "author": author, "ts": int(ts), "subject": subject,
                        "files": [f for f in files if f]})
    return commits


def rev_time(repo, rev):
    if not rev:
        return dt.datetime.now(dt.timezone.utc)
    ts = int(git(repo, "log", "-1", "--format=%ct", rev).strip())
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


# ---------------------------------------------------------------- module mapping

class ModuleMap:
    def __init__(self, model, paths):
        self.explicit = model.get("modules") or {}
        self.depth = max(1, int(model.get("module_depth", 1)))
        self.exclude = model.get("exclude", [])
        self.include = model.get("include", [])  # wins over exclude
        self.test_roots = set(model.get("test_roots", []))
        self.test_re = re.compile(model.get("test_file_pattern", r"$^"))
        self.roots = [r.strip("/") for r in model.get("module_roots", [])
                      if any(p.startswith(r.strip("/") + "/") for p in paths)]
        self.uses_roots = bool(self.roots)

    def excluded(self, path):
        if any(fnmatch.fnmatch(path, pat) for pat in self.include):
            return False
        return any(fnmatch.fnmatch(path, pat) for pat in self.exclude)

    def is_test(self, path):
        parts = path.split("/")
        return any(p in self.test_roots for p in parts[:-1]) or bool(self.test_re.match(parts[-1]))

    def _structural(self, path):
        for name, globs in self.explicit.items():
            if any(fnmatch.fnmatch(path, g) for g in globs):
                return name
        parts = path.split("/")
        dirs = parts[:-1]
        if not dirs:
            return "(root)"
        for root in self.roots:
            rparts = root.split("/")
            if parts[:len(rparts)] == rparts:
                rest = dirs[len(rparts):]
                return "/".join(rparts + rest[:self.depth]) if rest else root
        if self.uses_roots:
            return dirs[0]
        return "/".join(dirs[:self.depth])

    def module_of(self, path, known_modules=None):
        """Module a file belongs to (tests are attributed to the module they exercise), or None."""
        if self.excluded(path):
            return None
        parts = path.split("/")
        if parts[0] in self.test_roots and known_modules:
            # top-level tests dir: attribute by matching a module's last segment in the path
            hay = "/".join(parts[1:]).lower()
            best = None
            for m in known_modules:
                leaf = m.split("/")[-1].lower()
                if leaf and re.search(rf"(^|[/_\-.]){re.escape(leaf)}([/_\-.]|$)", hay):
                    if best is None or len(m) > len(best):
                        best = m
            return best
        if parts[0] in self.test_roots:
            return None
        return self._structural(path)


# ---------------------------------------------------------------- scoring

def _wmean(values, weights):
    tot = sum(weights.values()) or 1.0
    return sum(values[k] * w for k, w in weights.items()) / tot


def _level(x, th):
    if x >= th["high"]:
        return "High"
    if x >= th["medium"]:
        return "Medium"
    return "Low"


def compute(repo, model, rev=None):
    """Return {"rev","as_of","window_days","modules":[...sorted by score desc]} at rev (None = working tree)."""
    loc = line_counts(repo, rev)
    mm = ModuleMap(model, list(loc))
    end = rev_time(repo, rev)
    start = end - dt.timedelta(days=int(model.get("window_days", 180)))
    commits = commits_in_window(repo, rev, start, end)
    fix_re = re.compile(model.get("fix_pattern", r"$^"))

    src_loc, test_loc = defaultdict(int), defaultdict(int)
    for path, n in loc.items():
        if mm.excluded(path) or mm.is_test(path):
            continue
        m = mm.module_of(path)
        if m:
            src_loc[m] += n
    modules = set(src_loc)
    for path, n in loc.items():
        if mm.excluded(path) or not mm.is_test(path):
            continue
        m = mm.module_of(path, modules)
        if m in modules:
            test_loc[m] += n

    stats = {m: {"commits": 0, "fix_commits": 0, "cochange_commits": 0, "authors": set()} for m in modules}
    for c in commits:
        touched = {mm.module_of(f) for f in c["files"] if not mm.is_test(f)} & modules
        is_fix = bool(fix_re.search(c["subject"]))
        for m in touched:
            s = stats[m]
            s["commits"] += 1
            s["fix_commits"] += is_fix
            s["cochange_commits"] += len(touched) > 1
            s["authors"].add(c["author"])

    max_commits = max([s["commits"] for s in stats.values()] or [0])
    max_loc = max(src_loc.values() or [0])
    prior = float(model.get("fix_ratio_prior", 2))
    target = float(model.get("test_ratio_target", 0.5))
    crit = model.get("criticality", {})
    crit_default = float(model.get("criticality_default", 0.5))
    th = model.get("level_thresholds", {"medium": 0.33, "high": 0.66})

    rows = []
    for m in modules:
        s = stats[m]
        sig = {
            "churn": math.log1p(s["commits"]) / math.log1p(max_commits) if max_commits else 0.0,
            "fix_ratio": s["fix_commits"] / (s["commits"] + prior),
            "test_gap": max(0.0, 1 - test_loc[m] / (src_loc[m] * target)) if src_loc[m] else 0.0,
            "size": math.log1p(src_loc[m]) / math.log1p(max_loc) if max_loc else 0.0,
            "coupling": s["cochange_commits"] / (s["commits"] + prior),
            "criticality": float(crit.get(m, crit_default)),
        }
        lik = _wmean(sig, model["likelihood_weights"])
        imp = _wmean(sig, model["impact_weights"])
        ll, il = _level(lik, th), _level(imp, th)
        rows.append({
            "module": m, "tier": TIER_GRID.get((ll, il), "Low"),
            "score": round(lik * imp, 3), "likelihood": round(lik, 3), "impact": round(imp, 3),
            "likelihood_level": ll, "impact_level": il,
            "signals": {k: round(v, 3) for k, v in sig.items()},
            "commits": s["commits"], "fix_commits": s["fix_commits"], "authors": len(s["authors"]),
            "src_loc": src_loc[m], "test_loc": test_loc[m],
        })
    rows.sort(key=lambda r: (-TIER_ORDER.index(r["tier"]), -r["score"], r["module"]))
    head = git(repo, "rev-parse", "--short", rev or "HEAD").strip()
    return {"rev": head, "as_of": end.date().isoformat(), "window_days": model.get("window_days", 180),
            "model_version": model.get("version"), "modules": rows, "_map": mm}


def classify(repo, model, files, rev=None, result=None):
    """Tier of each touched module at rev. Modules that don't exist yet get `new_module_tier`
    (default Medium): no history means unknown risk, not low risk."""
    result = result or compute(repo, model, rev)
    new_tier = model.get("new_module_tier", "Medium")
    mm = result["_map"]
    by_name = {r["module"]: r for r in result["modules"]}
    file_map, touched = {}, {}
    for f in files:
        m = mm.module_of(f, set(by_name)) if mm.is_test(f) else mm.module_of(f)
        file_map[f] = m
        if m in by_name:
            touched[m] = by_name[m]
        elif m:
            touched.setdefault(m, {"module": m, "tier": new_tier, "score": 0.0, "new_module": True})
    tiers = [t["tier"] for t in touched.values()] or ["Low"]
    return {"rev": result["rev"], "as_of": result["as_of"],
            "max_tier": max(tiers, key=TIER_ORDER.index),
            "max_score": max([t.get("score", 0) for t in touched.values()] or [0]),
            "files": file_map, "modules": list(touched.values())}


# ---------------------------------------------------------------- rendering

def render_markdown(result, model_path, repo="."):
    rows = result["modules"]
    rel_model = os.path.relpath(os.path.abspath(model_path), start=repo)
    if rel_model.startswith(".."):  # bundled default outside the repo (e.g. installed by the GitHub Action)
        rel_model = "clean-eval default risk-model.json"
    lines = [START_MARK, "",
             f"_Generated by `/risk-heat-map` · as of {result['as_of']} · commit `{result['rev']}` · "
             f"{result['window_days']}-day window · model v{result['model_version']} (`{rel_model}`)_", ""]
    if not rows:
        lines += ["_No source modules detected yet._", "", END_MARK]
        return "\n".join(lines)

    cells = defaultdict(list)
    for r in rows:
        cells[(r["likelihood_level"], r["impact_level"])].append(f"`{r['module']}`")
    lines += ["| Likelihood ↓ / Impact → | Low | Medium | High |", "|---|---|---|---|"]
    for ll in reversed(LEVELS):
        out = [f"**{ll}**"]
        for il in LEVELS:
            tier = TIER_GRID.get((ll, il), "Low")
            out.append(f"{TIER_EMOJI[tier]} " + (", ".join(cells[(ll, il)]) or "·"))
        lines.append("| " + " | ".join(out) + " |")
    lines += ["",
              "| Module | Tier | Score | Likelihood | Impact | Commits | Fix ratio | Test gap | Coupling | Criticality | LOC | Authors |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        s = r["signals"]
        lines.append(
            f"| `{r['module']}` | {TIER_EMOJI[r['tier']]} {r['tier']} | {r['score']:.2f} | {r['likelihood']:.2f} | "
            f"{r['impact']:.2f} | {r['commits']} | {s['fix_ratio']:.2f} | {s['test_gap']:.2f} | {s['coupling']:.2f} | "
            f"{s['criticality']:.2f} | {r['src_loc']} | {r['authors']} |")
    lines += ["",
              "Tiers: 🟥 Critical · 🟧 High · 🟨 Medium · 🟩 Low. Score = likelihood × impact. "
              "Likelihood = churn, fix ratio, test gap; impact = size, co-change coupling, criticality.",
              "", END_MARK]
    return "\n".join(lines)


def write_readme(readme, block):
    text = open(readme).read() if os.path.exists(readme) else ""
    if START_MARK in text and END_MARK in text:
        pre, rest = text.split(START_MARK, 1)
        _, post = rest.split(END_MARK, 1)
        text = pre + block + post
    else:
        m = re.search(rf"^{re.escape(SECTION_HEADING)}[ \t]*$", text, re.M)
        if m:
            nxt = re.search(r"^## ", text[m.end():], re.M)
            end = m.end() + nxt.start() if nxt else len(text)
            text = text[:m.end()] + "\n\n" + block + "\n\n" + text[end:].lstrip("\n")
        else:
            text = text.rstrip("\n") + f"\n\n{SECTION_HEADING}\n\n{block}\n"
    with open(readme, "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")


# ---------------------------------------------------------------- CLI

def _public(result):
    return {k: v for k, v in result.items() if not k.startswith("_")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--model", help="default: <repo>/.clean-eval/risk-model.json, else the bundled default")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("heatmap")
    h.add_argument("--rev", help="compute as of this revision (default: working tree, window ends now)")
    h.add_argument("--json", action="store_true")
    h.add_argument("--write-readme", action="store_true")
    h.add_argument("--readme", default=None, help="default: <repo>/README.md")

    c = sub.add_parser("classify")
    c.add_argument("--rev", help="revision to score at (use the PR base)")
    c.add_argument("--files", nargs="*", default=[])
    c.add_argument("--files-from", help="file with one path per line, '-' for stdin")

    a = ap.parse_args(argv)
    repo = repo_root(a.repo)
    model_path = resolve_model_path(repo, a.model)
    model = load_model(model_path)

    if a.cmd == "heatmap":
        result = compute(repo, model, a.rev)
        if a.json:
            print(json.dumps(_public(result), indent=2))
            return
        block = render_markdown(result, model_path, repo)
        if a.write_readme:
            readme = a.readme or os.path.join(repo, "README.md")
            write_readme(readme, block)
            print(f"updated {readme} ({len(result['modules'])} modules, model {model_path})", file=sys.stderr)
        print(block)
    else:
        files = list(a.files)
        if a.files_from:
            src = sys.stdin if a.files_from == "-" else open(a.files_from)
            files += [l.strip() for l in src if l.strip()]
        print(json.dumps(classify(repo, model, files, a.rev), indent=2))


if __name__ == "__main__":
    main()
