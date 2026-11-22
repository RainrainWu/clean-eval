"""Fixtures for every layer's tests: the hermetic git config, throwaway repositories, fake `gh` / `claude`, and the
domain models the tests feed the code under test; and at the end, those of the top-level tests: the codebase the
architecture rules read, action.sh run end to end, and the release check.

A fixture that builds data reads `request.param` for its customisation and has a default without one. A test sets the
param with `@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test or on its whole class; the
fixtures that are built from it (a `heatmap` from its `ratings`) follow."""

import copy
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import (
    AspectScores,
    Evidence,
    Facts,
    Heatmap,
    Judgement,
    ModuleRating,
    ModuleTests,
    Scores,
    SourceFile,
)
from clean_eval.domain.review import AgentReview, RiskReport
from tests.action import Action
from tests.architecture import Codebase
from tests.helpers import ROOT, Dates, FakeBin, Repo

GITCONFIG = """[user]
\tname = Test
\temail = test@example.com
[commit]
\tgpgsign = false
[tag]
\tgpgsign = false
[init]
\tdefaultBranch = main
[protocol "file"]
\tallow = always
"""

# A typical module's facts: 1000 lines in 5 files, 20 small typed functions, all linked to tests (unit and integration).
FACTS: dict[str, Any] = {
    "name": "src/x",
    "loc": 1000,
    "files": 5,
    "share": 0.1,
    "funcs": [(10, 2)] * 20,
    "typed_share": 1.0,
    "broad": 0,
    "swallow": 0,
    "todo": 0,
    "big_files": 0,
    "fan_in": 0,
    "fan_out": 0,
    "effect_counts": {},
    "effect_examples": {},
    "migrations": 0,
    "entry_flows": [],
    "flow_touch": 0.0,
    "keyword": False,
    "tooling": False,
    "graph_functions": 20,
    "linked_functions": 20,
    "linked_test_files": 3,
    "linked_test_loc": 1000.0,
    "test_scales": {"unit": 2, "integration": 1},
    "line_rate": None,
}
# A source file's defaults: a spec of `source_files` adds at least `path` and `loc`.
SOURCE_FILE: dict[str, Any] = {"text": "", "is_test": False, "blob": ""}
# A rated module: its aspect scores, its overall score (None: the worst aspect) and its facts (None, or Facts overrides).
RATING: dict[str, Any] = {
    "name": "m",
    "loc": 100,
    "biz": 40,
    "effects": 10,
    "entropy": 20,
    "unverified": 30,
    "overall": None,
    "facts": None,
}
# The repository action.sh runs on, by default: one commit of one module.
WORKSPACE: list[dict[str, Any]] = [{"message": "init", "files": {"src/a/x.py": "1\n"}}]
# The pull request the fake `gh` shows, by default: #7, from branch feature into main.
PULL_REQUEST: dict[str, Any] = {
    "number": 7,
    "title": "t",
    "body": "",
    "author": {"login": "dev"},
    "url": "u",
    "baseRefName": "main",
    "headRefName": "feature",
    "baseRefOid": "a" * 40,
    "headRefOid": "b" * 40,
    "isCrossRepository": False,
}
# What a pull request plants at the root of a checkout: modules named after the standard library's and the dependencies'.
PLANTED = ("json", "sys", "datetime", "shlex", "argparse", "os", "re", "pydantic", "typing_extensions", "click")
RELEASE_SCRIPT = Path(ROOT) / ".github" / "scripts" / "check_release.py"
PYPROJECT = '[project]\nname = "x"\nversion = "%s"\n'  # % the version

# A change under review: the rules of the base, a finding the agent proposes, the change's risk.
# A review-rules.md: a blocker rule that applies from the Low band, and a major and a minor one from Moderate.
REVIEW_RULES = """
| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | dependency rule |
| Q-02 | major | Moderate | failure paths |
| Q-04 | minor | Moderate | clarity |
"""
# A finding as the review agent writes it in result.json.
FINDING: dict[str, Any] = {"rule": "Q-02", "severity": "major", "file": "a.py", "line": 3, "summary": "s"}
# risk.json of PR 1, which changes a.py and is in the High band.
RISK_REPORT: dict[str, Any] = {
    "pr": 1,
    "base": "b" * 40,
    "head": "c" * 40,
    "max_tier": "High",
    "max_score": 52,
    "model_source": "clean-eval default",
    "model_version": 2,
    "rules_source": "clean-eval default",
    "rules_version": "abcd1234",
    "files_changed": 1,
    "modules": [],
    "files": {"a.py": None},
    "ignored": [],
    "notes": [],
}


# The machine: git config, throwaway repositories, fake executables.


@pytest.fixture(autouse=True, scope="session")
def hermetic_git(
    tmp_path_factory,
):
    """Every git call, in the tests and in the code under test, ignores the developer's git config."""
    config = tmp_path_factory.mktemp("git") / "gitconfig"
    config.write_text(GITCONFIG)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_CONFIG_GLOBAL", str(config))
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield


@pytest.fixture
def repo(
    tmp_path,
):
    """A throwaway git repository (tests.helpers.Repo), on branch main."""
    return Repo(tmp_path / "repo")


@pytest.fixture
def fake_bin(
    tmp_path,
    monkeypatch,
):
    """Fake `gh` and `claude` executables (tests.helpers.FakeBin), first on PATH."""
    fake = FakeBin(tmp_path / "bin")
    for name, value in fake.env().items():
        monkeypatch.setenv(name, value)
    return fake


# The domain models the code under test reads.


@pytest.fixture
def today(
    request,
):
    """The date the code under test is run on, as the Clock gives it: "2026-11-16", or `request.param`."""
    return getattr(request, "param", "2026-11-16")


@pytest.fixture
def policy(
    request,
):
    """The risk policy: the defaults, or the overrides in `request.param` ({knob: value}, as risk-model.json holds)."""
    return Policy.from_overrides(getattr(request, "param", None))


@pytest.fixture
def facts(
    request,
):
    """A module's Facts: FACTS with the fields in `request.param` replaced."""
    return Facts.model_validate({**FACTS, **getattr(request, "param", {})})


@pytest.fixture
def source_files(
    request,
):
    """SourceFiles, one per spec in `request.param` (dicts of SourceFile fields: `path` and `loc`, plus `text`,
    `is_test` and `blob` when they matter); none when not parametrized."""
    return [SourceFile.model_validate({**SOURCE_FILE, **spec}) for spec in getattr(request, "param", [])]


@pytest.fixture
def ratings(
    request,
):
    """ModuleRatings, one per spec in `request.param` (dicts over RATING's keys); one module "m" when not parametrized.
    A rating's overall score is the rules' to compute (use_cases/rules/heatmap.py); here it is the spec's `overall`, by
    default the worst aspect, and its baseline is its aspect scores."""
    built = []
    for spec in getattr(request, "param", [{}]):
        s = {**RATING, **spec}
        aspect = AspectScores(biz=s["biz"], effects=s["effects"], entropy=s["entropy"], unverified=s["unverified"])
        worst = max(v for v in (s["biz"], s["effects"], s["entropy"], s["unverified"]) if v is not None)
        built.append(
            ModuleRating(
                name=s["name"],
                loc=s["loc"],
                files=1,
                scores=Scores(**aspect.model_dump(), overall=worst if s["overall"] is None else s["overall"]),
                baseline=aspect,
                evidence=Evidence(biz=["e"], effects=["e"], entropy=["e"], unverified=["e"]),
                tests=ModuleTests(functions=0, tested=0, coverage=None, files=0, loc=0, ratio=0),
                facts=None if s["facts"] is None else Facts.model_validate({**FACTS, **s["facts"]}),
            )
        )
    return built


@pytest.fixture
def rating(
    ratings,
):
    """The first of `ratings`: module "m", unless `ratings` is parametrized."""
    return ratings[0]


@pytest.fixture
def heatmap(
    ratings,
    today,
):
    """The Heatmap of repository "demo" at main (abc1234), rated `today`, holding `ratings`."""
    return Heatmap(
        repo="demo",
        ref="main",
        sha="abc1234",
        generated_at=today,
        loc_total=sum(m.loc for m in ratings),
        modules=ratings,
    )


@pytest.fixture
def judgement(
    request,
):
    """A reviewer's Judgement, validated from `request.param` ({"modules": {name: {aspect: {"level", "reason"}}}});
    no levels when not parametrized."""
    return Judgement.model_validate(getattr(request, "param", {"modules": {}}))


# A change under review, for the review's rules and the CI commands that finalize it.


@pytest.fixture
def review_rules(
    request,
):
    """The text of a review-rules.md: REVIEW_RULES (CA-01 a blocker from the Low band, Q-02 major and Q-04 minor from
    Moderate), or `request.param`."""
    return getattr(request, "param", REVIEW_RULES)


@pytest.fixture
def finding(
    request,
):
    """A finding as the review agent writes it in result.json (a dict, not validated): FINDING (rule Q-02, major, on
    line 3 of a.py) with the fields in `request.param` replaced."""
    return {**FINDING, **getattr(request, "param", {})}


@pytest.fixture
def agent_review(
    request,
    finding,
):
    """The review agent's result.json, validated as an AgentReview: the one finding `finding`, with the result.json
    fields in `request.param` replaced or added (`{"findings": []}` for none, or a verdict of the agent's own)."""
    return AgentReview.model_validate({"findings": [finding], **getattr(request, "param", {})})


@pytest.fixture
def risk_report(
    request,
):
    """The RiskReport (risk.json) of the change under review: RISK_REPORT (PR 1 changes a.py, High band, score 52) with
    the fields in `request.param` replaced."""
    return RiskReport.model_validate({**RISK_REPORT, **getattr(request, "param", {})})


@pytest.fixture
def pull_request(
    request,
):
    """A pull request as the code host describes it (`gh pr view --json`): PULL_REQUEST (#7 by dev, from feature onto
    main, commits "b" * 40 onto "a" * 40) with the fields in `request.param` replaced. action.sh's runs replace its
    commits with the workspace's main and HEAD."""
    return copy.deepcopy({**PULL_REQUEST, **getattr(request, "param", {})})


# The top-level tests: the codebase the architecture rules read (test_architecture.py), action.sh run end to end with
# fake `gh` and `claude` (test_action_sh.py), and the release check run on throwaway repositories (test_release.py).


@pytest.fixture
def codebase():
    """This checkout, read as code (tests.architecture.Codebase): the package, its tests and the review rules."""
    return Codebase(ROOT)


# action.sh: the workspace it runs on, what the fakes do, and its runs.


@pytest.fixture
def workspace_links(
    request,
):
    """The symlinks `workspace` gets after its commits, untracked: {path: target}, from `request.param`; none when not
    parametrized."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def workspace(
    request,
    repo,
    workspace_links,
):
    """The repository action.sh runs on: `repo` with the commits in `request.param` (by default WORKSPACE), oldest
    first, each a dict of `message` and `files` ({path: text}), and optionally `days_ago` (its date; 1 when left out)
    and `branch` (a new branch to commit it on, from the current one); then `workspace_links`."""
    for commit in getattr(request, "param", WORKSPACE):
        if "branch" in commit:
            repo.git("checkout", "-q", "-b", commit["branch"])
        repo.commit(commit["message"], commit.get("files", {}), when=Dates.days_ago(commit.get("days_ago", 1)))
    for path, target in workspace_links.items():
        (Path(repo.path) / path).symlink_to(target)
    return repo


@pytest.fixture
def gh_replies(
    request,
):
    """The fake `gh`'s other replies: {key: reply}, as tests.helpers.FAKE_GH reads them (`auth`, `post_review`,
    `list_reviews`, ...), from `request.param`; none when not parametrized, so every other call succeeds with `{}`."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def claude_plan(
    request,
):
    """What the fake `claude` does, as tests.helpers.FAKE_CLAUDE reads it: the files it writes to the agent's folder
    (`agent_files`) and to the workspace (`workspace_files`), what it says and its exit status, from `request.param`;
    when not parametrized it writes nothing."""
    return dict(getattr(request, "param", {}))


@pytest.fixture
def action(
    tmp_path,
    fake_bin,
    workspace,
    gh_replies,
    claude_plan,
    pull_request,
):
    """action.sh, ready to run on `workspace` as a GitHub runner runs it (tests.action.Action), with its output folder,
    step outputs and step summary in the temporary folder. Each run first gives the fake `claude` its `claude_plan`, and
    the fake `gh` its `gh_replies` and the `pull_request`."""
    return Action(tmp_path, fake_bin, workspace, gh_replies, claude_plan, pull_request)


@pytest.fixture
def action_run(
    request,
    action,
):
    """The run of action.sh by `action` (a subprocess.CompletedProcess) with the inputs in `request.param`: {input:
    value}, so {"skill": "pr-review", "pr": "7"} sets CE_SKILL and CE_PR. The risk heat map when not parametrized. A
    test that runs it again, or after changing the workspace, calls `action.run`."""
    return action.run(**getattr(request, "param", {"skill": "risk-heat-map"}))


@pytest.fixture
def shallow_clone(
    request,
    workspace,
    tmp_path,
):
    """A shallow clone of `workspace`, holding its last `request.param` commits (1 when not parametrized), as
    actions/checkout makes one by default."""
    clone = tmp_path / "shallow"
    depth = str(getattr(request, "param", 1))
    subprocess.run(["git", "clone", "-q", "--depth", depth, "file://" + workspace.path, str(clone)], check=True)
    return clone


@pytest.fixture
def fake_python(
    request,
    tmp_path,
):
    """An executable to give action.sh as its Python: the script in `request.param`, its #! line included; by default a
    shell script that exits 1, as an interpreter that can't import pydantic does on `-c 'import pydantic'`."""
    path = tmp_path / "python-without-pydantic"
    path.write_text(getattr(request, "param", "#!/bin/sh\nexit 1\n"))
    path.chmod(0o755)
    return path


@pytest.fixture
def crashing_package(
    request,
    tmp_path,
):
    """A folder that Python runs as a program (`python <folder>`), its __main__.py being `request.param`; by default
    one that raises, so the program exits with status 1."""
    folder = tmp_path / "crashing"
    folder.mkdir()
    (folder / "__main__.py").write_text(getattr(request, "param", "raise RuntimeError('boom')\n"))
    return folder


@pytest.fixture
def planted_modules(
    request,
    workspace,
    tmp_path,
):
    """Modules a pull request plants at the root of `workspace`, committed on its current branch: one per name in
    `request.param` (PLANTED when not parametrized), each appending its name to a marker file if it is ever imported.
    Returns the marker's path."""
    marker = tmp_path / "planted-module-ran"
    planted = {
        name + ".py": "open(%r, 'a').write(%r)\n" % (str(marker), name + "\n")
        for name in getattr(request, "param", PLANTED)
    }
    workspace.commit("feat: plant", planted)
    return marker


# The release check.


@pytest.fixture(scope="session")
def check_release():
    """The release check (.github/scripts/check_release.py), loaded as a module."""
    spec = importlib.util.spec_from_file_location("check_release", RELEASE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def released(
    request,
    repo,
):
    """`repo` with release 1.0.0 made five days ago (pyproject.toml saying 1.0.0, tagged v1.0.0), then the steps in
    `request.param` four days ago, in order; none when not parametrized. A step is a dict of, in the order they apply:
    `branch` (a new branch to switch to); `version` (commit release <version>: pyproject.toml saying it) or `message`
    and `files` (commit them); and `tag` (tag the new commit, or the revision `at`; `annotated` for an annotated tag).
    A version is tagged v<version> unless `tag` says otherwise."""
    first = repo.commit("release 1.0.0", {"pyproject.toml": PYPROJECT % "1.0.0"}, when=Dates.days_ago(5))
    repo.git("tag", "v1.0.0", first)
    for step in getattr(request, "param", []):
        if "branch" in step:
            repo.git("checkout", "-q", "-b", step["branch"])
        made = None
        if "version" in step:
            files = {"pyproject.toml": PYPROJECT % step["version"]}
            made = repo.commit("release " + step["version"], files, when=Dates.days_ago(4))
        elif "message" in step:
            made = repo.commit(step["message"], step.get("files", {}), when=Dates.days_ago(4))
        tag = step.get("tag", "v" + step["version"] if "version" in step else None)
        if tag is not None:
            annotation = ["-a", "-m", "release"] if step.get("annotated") else []
            repo.git("tag", *annotation, tag, step.get("at", made))
    return repo


@pytest.fixture
def release_check(
    request,
    released,
):
    """The release check run on `released` as the release workflow runs it (`--branch main`), for the tag in
    `request.param` (v1.0.0 when not parametrized): the finished process, with the step outputs on stdout and any
    ::error:: on stderr."""
    tag = getattr(request, "param", "v1.0.0")
    command = [sys.executable, "-I", str(RELEASE_SCRIPT), tag, "--branch", "main"]
    return subprocess.run(command, cwd=released.path, capture_output=True, text=True)
