"""The git gateway (external/git.py), against real throwaway repositories; and the contract it shares with the use
cases' MemoryGit (tests/use_cases/fakes.py): the contract checks run against both, over the same history."""

import datetime as dt
import os

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.git import GitGateway
from tests.helpers import Repo, days_ago
from tests.use_cases.fakes import MemoryGit, blob_id


def test_diff_show_and_odd_names(repo):
    base = repo.commit("init", {"a.py": "1\n"}, when=days_ago(2))
    head = repo.commit("odd names", {"src/résumé.py": "2\n", 'src/we"ird.py': "3\n"}, when=days_ago(1))
    git = GitGateway(repo.path)
    assert sorted(git.diff_files(base, head)) == sorted(["src/résumé.py", 'src/we"ird.py'])
    assert git.show(head, "src/résumé.py") == "2\n"
    assert git.show(base, "src/résumé.py") is None  # not there at that revision
    assert git.show(head, "src") is None  # a folder is not a file
    with pytest.raises(HeatmapError):  # a real failure is not "not there"
        git.show("0" * 40, "a.py")
    repo.git("remote", "add", "origin", "git@github.com:org/My-Repo.git")
    assert git.repo_name() == "My-Repo"


def test_history_reads(repo):
    first = repo.commit("init", {"a.py": "1\n"}, when=days_ago(3))
    second = repo.commit("feat: b (#2)", {"b.py": "2\n"}, when=days_ago(2))
    git = GitGateway(repo.path)
    log = git.first_parent_log("main", days_ago(10).date().isoformat(), days_ago(0).date().isoformat())
    assert [(sha, parents, subject) for sha, parents, _, _, subject in log] == [
        (second, [first], "feat: b (#2)"),
        (first, [], "init"),
    ]
    assert git.default_branch() == "main"  # no origin: the local main
    assert git.has_commit(first)
    assert not git.has_commit("0" * 40)
    with pytest.raises(HeatmapError):  # not here, and no origin to fetch it from
        git.ensure_commit("0" * 40, "refs/heads/nope")
    assert "feat: b (#2)" in git.file_log(second, "b.py", 10)
    assert "+2" in git.commit_patch(second, ["b.py"])


def test_commands_are_not_composed_outside():
    assert not hasattr(GitGateway, "run")  # callers ask for operations, not for git command lines


# The contract: two commits (src/a.py and docs/n.md; then src/a.py changed, src/b.py added, docs/n.md removed), built in
# MemoryGit and in a real repository. Every check below runs on both.

FIRST, SECOND = dt.datetime(2026, 3, 1, 12, tzinfo=dt.timezone.utc), dt.datetime(2026, 3, 2, 12, tzinfo=dt.timezone.utc)


def memory_history(tmp_path):
    git = MemoryGit()
    first = git.commit("init", {"src/a.py": "a\n", "docs/n.md": "n\n"}, when=FIRST, author="Test")
    second = git.commit(
        "feat: b (#2)", {"src/a.py": "a2\n", "src/b.py": "b\n"}, when=SECOND, author="Test", remove=["docs/n.md"]
    )
    return git, first, second


def gateway_history(tmp_path):
    repo = Repo(tmp_path / "repo")
    first = repo.commit("init", {"src/a.py": "a\n", "docs/n.md": "n\n"}, when=FIRST)
    os.remove(os.path.join(repo.path, "docs", "n.md"))
    second = repo.commit("feat: b (#2)", {"src/a.py": "a2\n", "src/b.py": "b\n"}, when=SECOND)
    return GitGateway(repo.path), first, second


@pytest.fixture(params=[memory_history, gateway_history], ids=["MemoryGit", "GitGateway"])
def history(request, tmp_path):
    """(git, first commit, second commit) of the contract's history, once per implementation."""
    return request.param(tmp_path)


@pytest.fixture
def git(history):
    return history[0]


@pytest.fixture
def first(history):
    return history[1]


@pytest.fixture
def second(history):
    return history[2]


def test_revisions(git, first, second):
    assert len(git.rev_parse("HEAD")) == 40
    assert git.rev_parse("HEAD", short=True) == second[:7]
    assert git.rev_parse(second[:10]) == second
    assert git.has_commit(first)
    assert not git.has_commit("0" * 40)
    assert git.merge_base(first, second) == first
    assert (git.branch(), git.default_branch()) == ("main", "main")
    with pytest.raises(HeatmapError):
        git.ensure_commit("0" * 40, "refs/heads/nope")


def test_files_and_contents(git, first, second):
    assert git.ls_files() == ["src/a.py", "src/b.py"]
    assert git.ls_tree(first) == [("docs/n.md", blob_id("n\n"), 2), ("src/a.py", blob_id("a\n"), 2)]
    assert git.read_blobs([blob_id("a\n"), blob_id("a2\n"), "0" * 40]) == {
        blob_id("a\n"): "a\n",
        blob_id("a2\n"): "a2\n",
    }  # an unknown blob is left out
    assert git.show(second, "src/b.py") == "b\n"
    assert git.show(first, "src/b.py") is None  # not there at that revision
    assert git.show(second, "src") is None  # a folder is not a file
    with pytest.raises(HeatmapError):  # a bad revision is not "not there"
        git.show("0" * 40, "src/a.py")


def test_changes(git, first, second):
    assert git.diff_files(first, second) == ["docs/n.md", "src/a.py", "src/b.py"]
    patch = git.diff(first, second, "src/a.py")
    assert patch.startswith("diff --git a/src/a.py b/src/a.py\n")
    assert "\n-a\n+a2\n" in patch
    assert git.diff(first, second).count("diff --git ") == 3
    assert "+b" in git.commit_patch(second, ["src/b.py"])


def test_history(git, first, second):
    log = git.first_parent_log("main", "2026-02-28", "2026-03-03")
    assert [(sha, parents, author, ts, subject) for sha, parents, author, ts, subject in log] == [
        (second, [first], "Test", int(SECOND.timestamp()), "feat: b (#2)"),
        (first, [], "Test", int(FIRST.timestamp()), "init"),
    ]
    assert git.first_parent_log("main", "2026-03-10", "2026-03-20") == []
    lines = git.file_log(second, "src/a.py").splitlines()
    assert [line.split()[0] for line in lines] == [second[:7], first[:7]]  # newest first
    assert lines[0].endswith("feat: b (#2)")
