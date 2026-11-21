"""Shared test helpers: hermetic git, synthetic repos, and fake `gh` / `claude` executables."""
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, ".claude", "skills", "eval", "scripts"), os.path.join(ROOT, "ci")):
    if p not in sys.path:
        sys.path.insert(0, p)

_GITCONFIG_DIR = tempfile.mkdtemp(prefix="clean-eval-gitconfig-")
_GITCONFIG = os.path.join(_GITCONFIG_DIR, "gitconfig")
with open(_GITCONFIG, "w") as f:
    f.write("[user]\n\tname = Test\n\temail = test@example.com\n[commit]\n\tgpgsign = false\n"
            "[tag]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n[protocol \"file\"]\n\tallow = always\n")
from collections import Counter  # noqa: E402

from clean_eval.domain.entities import Facts, Heatmap, ModuleRating, SourceFile, facts_to_dict  # noqa: E402
from clean_eval.domain.scoring import overall  # noqa: E402

# Every git call in the tests (and in the code under test) ignores the developer's git config.
os.environ["GIT_CONFIG_GLOBAL"] = _GITCONFIG
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


def days_ago(n):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=n)).replace(microsecond=0)


class Repo:
    """A throwaway git repository with dated commits."""

    def __init__(self, path=None):
        self.path = path or tempfile.mkdtemp(prefix="clean-eval-repo-")
        self.git("init", "-q")

    def git(self, *args, env=None):
        r = subprocess.run(["git", "-C", self.path, *args], capture_output=True, text=True,
                           env={**os.environ, **(env or {})})
        if r.returncode != 0:
            raise RuntimeError(f"git {args}: {r.stderr}")
        return r.stdout.strip()

    def write(self, rel, content):
        full = os.path.join(self.path, rel)
        os.makedirs(os.path.dirname(full) or self.path, exist_ok=True)
        with open(full, "w") as f:
            f.write(content)

    def commit(self, message, files=None, when=None):
        """Write `files` ({path: content}), commit everything with `message` at `when` (datetime)."""
        for rel, content in (files or {}).items():
            self.write(rel, content)
        self.git("add", "-A")
        stamp = (when or days_ago(1)).isoformat()
        self.git("commit", "-q", "--allow-empty", "-m", message,
                 env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        return self.git("rev-parse", "HEAD")

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)


def make_executable(path, source):
    with open(path, "w") as f:
        f.write(f"#!{sys.executable}\n" + textwrap.dedent(source))
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


FAKE_GH = r'''
import json, os, sys
d = os.environ.get("FAKE_GH_DIR") or os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(d, "gh.json")) as f:
    cfg = json.load(f)
args = sys.argv[1:]
stdin = sys.stdin.read() if "--input" in args else ""
with open(os.path.join(d, "gh-calls.jsonl"), "a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin}) + "\n")

def reply(key):
    r = cfg.get(key, {"exit": 0, "out": "{}"})
    if isinstance(r, list):  # successive replies
        n_path = os.path.join(d, f"gh-count-{key}")
        n = 0
        if os.path.exists(n_path):
            with open(n_path) as f:
                n = int(f.read())
        with open(n_path, "w") as f:
            f.write(str(n + 1))
        r = r[min(n, len(r) - 1)]
    out = r.get("out", "")
    sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
    sys.stderr.write(r.get("err", ""))
    sys.exit(r.get("exit", 0))

if args[:2] == ["auth", "status"]:
    reply("auth")
if args[:2] == ["pr", "view"]:
    reply("pr_view_" + args[2] if "pr_view_" + args[2] in cfg else "pr_view")
if args[0] == "api":
    path = next(a for a in args[1:] if a.startswith("repos/"))
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    if path.endswith("/dismissals"):
        reply("dismiss")
    if path.endswith("/reviews") and method == "POST":
        reply("post_review")
    if path.endswith("/reviews"):
        reply("list_reviews")
    if path.endswith("/comments"):
        reply("inline_comments")
sys.exit(0)
'''

FAKE_CLAUDE = r'''
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(d, "claude.json")) as f:
    plan = json.load(f)
agent = os.environ["CE_GUARD_WRITE_ROOTS"].split(os.pathsep)[0]
with open(os.path.join(d, "claude-invocation.json"), "w") as f:
    json.dump({"argv": sys.argv[1:], "env": sorted(os.environ), "stdin": sys.stdin.read(), "cwd": os.getcwd()}, f)
for rel, content in plan.get("agent_files", {}).items():
    with open(os.path.join(agent, rel), "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))
for rel, content in plan.get("workspace_files", {}).items():
    os.makedirs(os.path.dirname(os.path.join(os.getcwd(), rel)), exist_ok=True)
    with open(os.path.join(os.getcwd(), rel), "w") as f:
        f.write(content if isinstance(content, str) else json.dumps(content))
print(plan.get("say", "done"))
sys.exit(plan.get("exit", 0))
'''


class FakeBin:
    """A bin dir with fake `gh` and `claude`, configured through JSON files next to them."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="clean-eval-bin-")
        make_executable(os.path.join(self.dir, "gh"), FAKE_GH)
        make_executable(os.path.join(self.dir, "claude"), FAKE_CLAUDE)
        self.gh({})
        self.claude({})

    def gh(self, cfg):
        _dump(os.path.join(self.dir, "gh.json"), cfg)

    def claude(self, plan):
        _dump(os.path.join(self.dir, "claude.json"), plan)

    def gh_calls(self):
        p = os.path.join(self.dir, "gh-calls.jsonl")
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(l) for l in f]

    def claude_invocation(self):
        p = os.path.join(self.dir, "claude-invocation.json")
        return _load(p) if os.path.exists(p) else None

    def env(self):
        return {"PATH": self.dir + os.pathsep + os.environ["PATH"], "FAKE_GH_DIR": self.dir}

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def _dump(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def _load(path):
    with open(path) as f:
        return json.load(f)


def read(path):
    with open(path) as f:
        return f.read()


def read_json(path):
    return json.loads(read(path))


def write_json(path, obj, **kw):
    write(path, json.dumps(obj, **kw))


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# ---------------------------------------------------------------- builders of domain entities

def facts(**kw):
    base = dict(name="src/x", loc=1000, files=5, share=0.1, funcs=[(10, 2)] * 20, typed_share=1.0, broad=0, swallow=0, todo=0,
                big_files=0, fan_in=0, fan_out=0, effect_counts=Counter(), effect_examples={}, migrations=0, entry_flows=[],
                flow_touch=0.0, keyword=False, tooling=False, graph_functions=20, linked_functions=20, linked_test_files=3,
                linked_test_loc=1000.0, test_scales=Counter(unit=2, integration=1), line_rate=None)
    base.update(kw)
    return Facts(**base)


def sf(path, loc, text="", test=False, blob=""):
    return SourceFile(path, text, loc, test, blob)


def rating(name="m", loc=100, biz=40, effects=10, entropy=20, unverified=30, fact=None):
    scores = {"biz": biz, "effects": effects, "entropy": entropy, "unverified": unverified}
    return ModuleRating(name=name, loc=loc, files=1, scores=dict(scores, overall=overall(scores)), baseline=dict(scores),
                        tests={"functions": 0, "tested": 0, "coverage": None, "files": 0, "loc": 0, "ratio": 0, "scales": {}},
                        evidence={k: ["e"] for k in scores}, top_files=[], facts=facts_to_dict(fact) if fact else None)


def heatmap(*modules):
    mods = list(modules) or [rating()]
    return Heatmap(repo="demo", ref="main", sha="abc1234", generated_at="2026-11-16", loc_total=sum(m.loc for m in mods),
                   modules=mods, notes=[])
