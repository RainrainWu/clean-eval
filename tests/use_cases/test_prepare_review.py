"""Preparing the CI reviewer's context (use_cases/prepare_review.py): the policy of the PR's base commit, and one patch
per changed file with an index, through fakes of the Git and FileSystem interfaces."""

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.policy import Policy
from clean_eval.domain.review import DiffIndex
from clean_eval.use_cases.interfaces import Git
from clean_eval.use_cases.prepare_review import policy_at, write_split_diff
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, fake


def test_one_patch_per_changed_file_and_an_index():
    git, fs = MemoryGit(), MemoryFileSystem()
    base = git.commit("init", {"src/a/old.py": "line\n" * 30, "src/a/keep.py": "k\n"})
    head = git.commit("change", {"src/a/keep.py": "k2\n", 'src/a/we"ird name.py': "w\n"}, remove=["src/a/old.py"])
    files = git.diff_files(base, head)
    index = write_split_diff(git, fs, "/ctx", base, head, files, {f: "src/a" for f in files})
    assert [e.file for e in index] == files
    for e in index:
        assert fs.read_text("/ctx/" + e.patch).startswith("diff --git a/%s " % e.file), e
        assert (e.module, e.truncated) == ("src/a", False)
    counts = {e.file: (e.added, e.removed) for e in index}
    assert counts == {"src/a/keep.py": (1, 1), "src/a/old.py": (0, 30), 'src/a/we"ird name.py': (1, 0)}
    assert DiffIndex.model_validate_json(fs.read_text("/ctx/diff/index.json")).root == index


def test_a_combined_diff_that_does_not_split_per_file_falls_back_to_one_diff_per_path():
    renamed = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
    per_path = {
        "new.py": "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n",
        "old.py": "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n",
    }
    git = fake(Git, diff=lambda base, head, path=None: renamed if path is None else per_path[path])
    index = write_split_diff(git, MemoryFileSystem(), "/ctx", "b", "h", ["new.py", "old.py"], {})
    assert [(e.file, e.added, e.removed) for e in index] == [("new.py", 1, 0), ("old.py", 0, 1)]


# A PR is judged by the policy of its base commit; a broken policy there falls back to the defaults, with a note.


def base_with(git, files):
    return git.commit("base", files)


def test_no_policy_file_means_the_defaults(git):
    base = base_with(git, {"a.py": "x = 1\n"})
    assert policy_at(git, base) == (Policy(), "clean-eval default", "{}", None)


def test_valid_overrides_at_the_base_are_applied(git):
    text = '{"unrated_band": "High", "business_weights": {"dependents": 0.4}}'
    base = base_with(git, {"a.py": "x = 1\n", ".clean-eval/risk-model.json": text})
    policy, source, raw, note = policy_at(git, base)
    assert (policy.unrated_band, policy.business_weights["dependents"]) == ("High", 0.4)
    assert (source, raw, note) == (".clean-eval/risk-model.json@" + base[:8], text, None)


def test_a_policy_changed_after_the_base_is_not_used(git):
    base = base_with(git, {"a.py": "x = 1\n"})
    git.commit("weaken", {".clean-eval/risk-model.json": '{"unrated_band": "Low"}'})
    assert policy_at(git, base)[0] == Policy()


def test_a_git_failure_is_not_mistaken_for_no_policy(git):
    base_with(git, {"a.py": "x = 1\n"})
    with pytest.raises(HeatmapError):
        policy_at(git, "0" * 40)


@pytest.mark.parametrize(
    ("text", "expect"),
    [('{"window_days": 30}', "v1 model"), ('{"effects_scale": 0}', "greater than 0"), ("{not json", "not valid JSON")],
    ids=["v1 model", "out of range", "not JSON"],
)
def test_an_invalid_or_v1_policy_falls_back_to_the_defaults_with_a_note(git, text, expect):
    base = base_with(git, {"a.py": "x = 1\n", ".clean-eval/risk-model.json": text})
    policy, source, raw, note = policy_at(git, base)
    assert (policy, source, raw) == (Policy(), "clean-eval default", "{}")
    assert note.startswith("ignored .clean-eval/risk-model.json at the base:"), note
    assert expect in note
