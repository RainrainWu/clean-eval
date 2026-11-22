"""The PR review's use cases (use_cases/review.py), with their dependencies mocked: a change's risk, and the context
the CI reviewer reads."""

from unittest.mock import call

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.policy import Policy
from clean_eval.domain.review import DiffIndex
from clean_eval.use_cases.review import assess_change, policy_at, write_split_diff

CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"
TREE = {
    "src/api/a.py": "a = 1\n" * 150,
    "src/api/b.py": "b = 1\n" * 150,
    "src/core/c.py": CORE,
    "tests/test_a.py": "x = 1\n",
}
# A change of the core module and the README, on a base that holds TREE.
CORE_CHANGE = [
    {"subject": "base", "files": TREE},
    {"subject": "change", "files": {"src/core/c.py": CORE + "# more\n", "README.md": "# r\n"}},
]
# A change that adds the first Python file.
FIRST_PYTHON = [
    {"subject": "base", "files": {"README.md": "# r\n"}},
    {"subject": "change", "files": {"src/new.py": "x = 1\n"}},
]
# A change that edits one file, removes one and adds one with an awkward name.
THREE_FILES = [
    {"subject": "init", "files": {"src/a/old.py": "line\n" * 30, "src/a/keep.py": "k\n"}},
    {
        "subject": "change",
        "files": {"src/a/keep.py": "k2\n", 'src/a/we"ird name.py': "w\n"},
        "remove": ["src/a/old.py"],
    },
]
CHANGED = ["src/a/keep.py", "src/a/old.py", 'src/a/we"ird name.py']
# A rename, which `git diff` shows as one section for two paths, and the per-path diffs.
RENAMED = "diff --git a/old.py b/new.py\nsimilarity index 100%\nrename from old.py\nrename to new.py\n"
NEW_PATCH = "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x\n"
OLD_PATCH = "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
POLICY_FILE = ".clean-eval/risk-model.json"


class TestAssessChange:
    """A change's risk, from the heat map of its base."""

    @pytest.mark.parametrize("history", [CORE_CHANGE], indirect=True)
    def test_the_risk_comes_from_the_heat_map_of_the_base(
        self,
        git,
        clock,
        policy,
        shas,
    ):
        heat, risk = assess_change(git, clock, shas[0], shas[1], policy)
        assert [m.module for m in risk.modules] == ["src/core"]
        assert risk.max_band == risk.modules[0].band
        assert risk.ignored == ["README.md"]
        git.diff_files.assert_called_once_with(shas[0], shas[1])

    @pytest.mark.parametrize("history", [FIRST_PYTHON], indirect=True)
    def test_a_base_without_python_rates_everything_unrated(
        self,
        git,
        clock,
        policy,
        shas,
        today,
    ):
        heat, risk = assess_change(git, clock, shas[0], shas[1], policy)
        assert (heat.repo, heat.sha, heat.generated_at) == ("demo", shas[0][:7], today)  # the base's snapshot
        assert (heat.modules, risk.max_band, risk.modules[0].rated) == ([], "Moderate", False)


class TestWriteSplitDiff:
    """The CI reviewer's diff: one patch per changed file, and an index of them."""

    @pytest.mark.parametrize("history", [THREE_FILES], indirect=True)
    def test_one_patch_per_changed_file_and_an_index(
        self,
        git,
        fs,
        memory_fs,
        shas,
    ):
        index = write_split_diff(git, fs, "/ctx", shas[0], shas[1], CHANGED, dict.fromkeys(CHANGED, "src/a"))
        assert [e.file for e in index] == CHANGED
        for e in index:
            assert memory_fs.read_text("/ctx/" + e.patch).startswith("diff --git a/%s " % e.file), e
            assert (e.module, e.truncated) == ("src/a", False)
        counts = {e.file: (e.added, e.removed) for e in index}
        assert counts == {"src/a/keep.py": (1, 1), "src/a/old.py": (0, 30), 'src/a/we"ird name.py': (1, 0)}
        assert DiffIndex.model_validate_json(memory_fs.read_text("/ctx/diff/index.json")).root == index

    @pytest.mark.parametrize("git", [{"diff.side_effect": [RENAMED, NEW_PATCH, OLD_PATCH]}], indirect=True)
    def test_a_diff_that_does_not_split_per_file_falls_back_to_one_diff_per_path(
        self,
        git,
        fs,
    ):
        index = write_split_diff(git, fs, "/ctx", "b", "h", ["new.py", "old.py"], {})
        assert [(e.file, e.added, e.removed) for e in index] == [("new.py", 1, 0), ("old.py", 0, 1)]
        assert git.diff.call_args_list == [call("b", "h"), call("b", "h", "new.py"), call("b", "h", "old.py")]


class TestPolicyAt:
    """A PR is judged by the policy of its base commit; a broken policy there falls back to the defaults, with a
    note."""

    @pytest.mark.parametrize("history", [[{"subject": "base", "files": {"a.py": "x = 1\n"}}]], indirect=True)
    def test_no_policy_file_means_the_defaults(
        self,
        git,
        shas,
    ):
        assert policy_at(git, shas[0]) == (Policy(), "clean-eval default", "{}", None)

    @pytest.mark.parametrize(
        "history",
        [
            [
                {
                    "subject": "base",
                    "files": {POLICY_FILE: '{"unrated_band": "High", "business_weights": {"dependents": 0.4}}'},
                }
            ]
        ],
        indirect=True,
    )
    def test_valid_overrides_at_the_base_are_applied(
        self,
        git,
        shas,
    ):
        policy, source, raw, note = policy_at(git, shas[0])
        assert (policy.unrated_band, policy.business_weights["dependents"]) == ("High", 0.4)
        assert (source, raw, note) == (POLICY_FILE + "@" + shas[0][:8], git.show(shas[0], POLICY_FILE), None)

    @pytest.mark.parametrize(
        "history",
        [
            [
                {"subject": "base", "files": {"a.py": "x = 1\n"}},
                {"subject": "weaken", "files": {POLICY_FILE: '{"unrated_band": "Low"}'}},
            ]
        ],
        indirect=True,
    )
    def test_a_policy_changed_after_the_base_is_not_used(
        self,
        git,
        shas,
    ):
        assert policy_at(git, shas[0])[0] == Policy()

    @pytest.mark.parametrize("git", [{"show.side_effect": HeatmapError("git show failed")}], indirect=True)
    def test_a_git_failure_is_not_mistaken_for_no_policy(
        self,
        git,
    ):
        with pytest.raises(HeatmapError):
            policy_at(git, "0" * 40)

    @pytest.mark.parametrize(
        ("history", "expect"),
        [
            ([{"subject": "base", "files": {POLICY_FILE: '{"window_days": 30}'}}], "v1 model"),
            ([{"subject": "base", "files": {POLICY_FILE: '{"effects_scale": 0}'}}], "greater than 0"),
            ([{"subject": "base", "files": {POLICY_FILE: "{not json"}}], "not valid JSON"),
        ],
        indirect=["history"],
        ids=["v1 model", "out of range", "not JSON"],
    )
    def test_an_invalid_or_v1_policy_falls_back_to_the_defaults_with_a_note(
        self,
        git,
        shas,
        expect,
    ):
        policy, source, raw, note = policy_at(git, shas[0])
        assert (policy, source, raw) == (Policy(), "clean-eval default", "{}")
        assert note is not None
        assert note.startswith("ignored .clean-eval/risk-model.json at the base:"), note
        assert expect in note
