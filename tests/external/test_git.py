"""The git gateway (external/git.py) on real throwaway repositories; and the contract it shares with the use cases'
MemoryGit (tests/use_cases/fakes.py): the contract checks run on both, over the same history. `git` holds the commits
of `history`, and `shas` are theirs."""

import subprocess

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.git import (
    GitGateway,
    split_patch,
)
from tests.helpers import Dates
from tests.use_cases.fakes import MemoryGit


@pytest.mark.parametrize("history", [lf("odd_names_history")], indirect=True)
class TestShowAndDiff:
    """Paths are taken literally, whatever their names; a path that is no file at a revision is None, while a bad
    revision is an error; and the repository is named after its origin."""

    def test_odd_names_are_taken_literally(
        self,
        git,
        shas,
    ):
        base, head = shas
        assert sorted(git.diff_files(base, head)) == sorted(["src/résumé.py", 'src/we"ird.py'])
        assert git.show(head, "src/résumé.py") == "2\n"

    def test_a_path_that_is_no_file_at_a_revision_is_none(
        self,
        git,
        shas,
    ):
        base, head = shas
        assert git.show(base, "src/résumé.py") is None  # not there at that revision
        assert git.show(head, "src") is None  # a folder is not a file

    def test_a_bad_revision_is_not_taken_for_a_missing_file(
        self,
        git,
    ):
        with pytest.raises(HeatmapError):
            git.show("0" * 40, "a.py")

    def test_the_repository_is_named_after_its_origin(
        self,
        git,
        repo,
    ):
        repo.git("remote", "add", "origin", "git@github.com:org/My-Repo.git")
        assert git.repo_name() == "My-Repo"


@pytest.mark.parametrize("history", [lf("merged_pr_history")], indirect=True)
class TestHistoryReads:
    """The history the retro reads: the first-parent log of a period, the default branch, commits that are or aren't
    here, and the log and patch of a file."""

    def test_the_first_parent_log_of_a_period(
        self,
        git,
        shas,
    ):
        first, second = shas
        log = git.first_parent_log("main", Dates.days_ago(10).date().isoformat(), Dates.days_ago(0).date().isoformat())
        assert [(sha, parents, subject) for sha, parents, _, _, subject in log] == [
            (second, [first], "feat: b (#2)"),
            (first, [], "init"),
        ]

    def test_without_an_origin_the_default_branch_is_the_local_main(
        self,
        git,
    ):
        assert git.default_branch() == "main"

    def test_a_commit_that_is_not_here_cannot_be_fetched_without_an_origin(
        self,
        git,
        shas,
    ):
        assert git.has_commit(shas[0])
        assert not git.has_commit("0" * 40)
        with pytest.raises(HeatmapError):  # not here, and no origin to fetch it from
            git.ensure_commit("0" * 40, "refs/heads/nope")

    def test_the_log_and_the_patch_of_a_file(
        self,
        git,
        shas,
    ):
        assert "feat: b (#2)" in git.file_log(shas[1], "b.py", 10)
        assert "+2" in git.commit_patch(shas[1], ["b.py"])


class TestSplitPatch:
    """A combined `git diff`, split into one patch per file."""

    @pytest.mark.parametrize(
        ("diff", "patches"),
        [(lf("diff_of_x_and_y"), [lf("patch_of_x"), lf("patch_of_y")]), ("", [])],
        ids=["two files", "no change"],
    )
    def test_one_patch_per_file(
        self,
        diff,
        patches,
    ):
        assert split_patch(diff) == patches


@pytest.mark.parametrize("history", [lf("rename_history")], indirect=True)
class TestFileDiffs:
    """A diff that git doesn't show one section per path of (a rename shows as one for two) is taken path by path."""

    def test_a_rename_is_diffed_path_by_path(
        self,
        git,
        shas,
    ):
        diffs = git.file_diffs(shas[0], shas[1], ["new.py", "old.py"])
        assert [(diff.path, diff.added, diff.removed, diff.hunks) for diff in diffs] == [
            ("new.py", 1, 0, [(1, 1)]),
            ("old.py", 0, 1, []),
        ]


class TestGitGateway:
    """What only a real repository shows: a shallow clone says so."""

    @pytest.mark.parametrize("history", [lf("contract_history")], indirect=True)
    def test_a_shallow_clone_says_so(
        self,
        repo,
        tmp_path,
    ):
        clone = tmp_path / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + repo.path, str(clone)], check=True)
        assert GitGateway(
            str(clone),
        ).is_shallow()


@pytest.mark.parametrize("history", [lf("contract_history")], indirect=True)
@pytest.mark.parametrize("git", ["MemoryGit", "GitGateway"], indirect=True)
class TestMemoryGitAgreesWithTheGateway:
    """The contract: every check runs on the in-memory MemoryGit and on GitGateway over the same history, so the use
    cases' tests run on a fake that behaves like the real thing."""

    def test_revisions(
        self,
        git,
        shas,
    ):
        first, second = shas
        assert len(git.rev_parse("HEAD")) == 40
        assert git.rev_parse("HEAD", short=True) == second[:7]
        assert git.rev_parse(second[:10]) == second
        assert git.has_commit(first)
        assert not git.has_commit("0" * 40)
        assert git.merge_base(first, second) == first
        assert (git.branch(), git.default_branch()) == ("main", "main")
        with pytest.raises(HeatmapError):
            git.ensure_commit("0" * 40, "refs/heads/nope")

    def test_files_and_contents(
        self,
        git,
        shas,
    ):
        first, second = shas
        assert git.ls_files() == ["src/a.py", "src/b.py"]
        assert git.ls_tree(first) == [
            ("docs/n.md", MemoryGit.blob_id("n\n"), 2),
            ("src/a.py", MemoryGit.blob_id("a\n"), 2),
        ]
        assert git.read_blobs([MemoryGit.blob_id("a\n"), MemoryGit.blob_id("a2\n"), "0" * 40]) == {
            MemoryGit.blob_id("a\n"): "a\n",
            MemoryGit.blob_id("a2\n"): "a2\n",
        }  # an unknown blob is left out
        assert git.show(second, "src/b.py") == "b\n"
        assert git.show(first, "src/b.py") is None  # not there at that revision
        assert git.show(second, "src") is None  # a folder is not a file
        with pytest.raises(HeatmapError):  # a bad revision is not "not there"
            git.show("0" * 40, "src/a.py")

    def test_changes(
        self,
        git,
        shas,
    ):
        first, second = shas
        changed = git.diff_files(first, second)
        assert changed == ["docs/n.md", "src/a.py", "src/b.py"]
        diffs = git.file_diffs(first, second, changed)
        assert [(diff.path, diff.added, diff.removed, diff.hunks) for diff in diffs] == [
            ("docs/n.md", 0, 1, []),  # removed: none of its lines is left
            ("src/a.py", 1, 1, [(1, 1)]),
            ("src/b.py", 1, 0, [(1, 1)]),
        ]
        assert diffs[1].patch.startswith("diff --git a/src/a.py b/src/a.py\n")
        assert "\n-a\n+a2\n" in diffs[1].patch
        assert "+b" in git.commit_patch(second, ["src/b.py"])

    def test_the_ids_of_contents_are_gits(
        self,
        git,
    ):
        assert git.blob_id("hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"  # `git hash-object` of "hi\n"

    def test_history(
        self,
        git,
        shas,
        history,
    ):
        first, second = shas
        log = git.first_parent_log("main", "2026-02-28", "2026-03-03")
        assert [(sha, parents, author, ts, subject) for sha, parents, author, ts, subject in log] == [
            (second, [first], "Test", int(history[1]["when"].timestamp()), "feat: b (#2)"),
            (first, [], "Test", int(history[0]["when"].timestamp()), "init"),
        ]
        assert git.first_parent_log("main", "2026-03-10", "2026-03-20") == []
        lines = git.file_log(second, "src/a.py").splitlines()
        assert [line.split()[0] for line in lines] == [second[:7], first[:7]]  # newest first
        assert lines[0].endswith("feat: b (#2)")
