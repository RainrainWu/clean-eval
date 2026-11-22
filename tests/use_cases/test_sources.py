"""The sources a rating reads (use_cases/sources.py): the tracked Python files of a working tree as they are on disk,
through the Git and FileSystem interfaces, or of a revision, from git objects."""

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.sources import (
    GENERATED,
    MAX_FILE_BYTES,
    content_id,
    count_loc,
    read_revision,
    read_working_tree,
)
from tests.use_cases.fakes import blob_id

P = Policy()


@pytest.fixture
def git(git):
    """Two commits; the second adds a generated file, a huge one, and one the working tree has lost. A symlink is staged."""
    git.commit("init", {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"})
    git.commit(
        "change",
        {
            "src/a.py": "def f():\n    return 2\n",
            "src/x_pb2.py": "X = 1\n",
            "src/gone.py": "G = 1\n",
            "src/huge.py": "#\n" * (MAX_FILE_BYTES // 2 + 1),
            "docs/n.md": "n\n",
        },
    )
    git.stage("src/link.py")
    return git


@pytest.fixture
def fs(fs, git):
    """The working tree: the last commit without src/gone.py, src/a.py edited, an untracked file and the symlink."""
    for path, text in git.commits[-1]["tree"].items():
        if path != "src/gone.py":
            fs.write_text("/repo/" + path, text)
    fs.write_text("/repo/src/a.py", "def f():\n    return 3\n")  # edited, not committed
    fs.write_text("/repo/src/untracked.py", "U = 1\n")
    fs.link("/repo/src/link.py", "/etc/hosts")
    return fs


def test_a_working_tree_is_its_tracked_python_files_as_they_are_on_disk(git, fs):
    snap, read = read_working_tree(git, fs, P)
    files = {f.path: f for f in read}
    assert sorted(files) == ["src/a.py", "tests/test_a.py"]  # not generated, linked, gone, huge or untracked
    assert files["src/a.py"].text == "def f():\n    return 3\n"
    assert files["src/a.py"].blob == blob_id("def f():\n    return 3\n")  # git's id: caches are shared
    assert files["tests/test_a.py"].is_test
    assert (snap.repo, snap.ref, snap.sha, snap.root) == ("demo", "main", git.rev_parse("HEAD", True), "/repo")


def test_a_revision_is_read_from_git_objects_sharing_their_contents(git):
    old_sha = git.commits[0]["sha"]
    blobs = {}
    snap, old = read_revision(git, old_sha, P, blobs)
    assert {f.path: f.text for f in old} == {
        "src/a.py": "def f():\n    return 1\n",
        "tests/test_a.py": "from src.a import f\n",
    }
    assert (snap.ref, snap.sha, len(blobs)) == (old_sha, old_sha[:7], 2)
    _, head = read_revision(git, "HEAD", P, blobs)
    assert sorted(f.path for f in head) == ["src/a.py", "src/gone.py", "tests/test_a.py"]  # not generated or huge
    assert len(blobs) == 4  # the unchanged test file is read once


def test_helpers():
    assert count_loc("a = 1\n\n# note\n  \nb = 2\n") == 2
    assert all(GENERATED.search("/" + p) for p in ("x_pb2.py", "a/generated/b.py", "vendor/x.py"))
    assert content_id("hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"  # `git hash-object` of "hi\n"
