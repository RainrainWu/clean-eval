"""The PR review's use cases (use_cases/review.py), with their dependencies mocked: a change's risk, the context the
CI reviewer reads, and a CI run without a PR to review."""

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.domain.errors import (
    HeatmapError,
    RunFailed,
)
from clean_eval.domain.policy import Policy
from clean_eval.domain.review import DiffIndex
from clean_eval.use_cases.review import (
    assess_change,
    policy_at,
    run_review_action,
    write_split_diff,
)


class TestAssessChange:
    """A change's risk, from the heat map of its base."""

    @pytest.mark.parametrize("history", [lf("core_change")], indirect=True)
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

    @pytest.mark.parametrize("history", [lf("first_python_change")], indirect=True)
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

    @pytest.mark.parametrize("history", [lf("three_file_change")], indirect=True)
    def test_one_patch_per_changed_file_and_an_index(
        self,
        git,
        fs,
        memory_fs,
        shas,
        three_changed_paths,
    ):
        index = write_split_diff(
            git, fs, "/ctx", shas[0], shas[1], three_changed_paths, dict.fromkeys(three_changed_paths, "src/a")
        )
        assert [e.file for e in index] == three_changed_paths
        for e in index:
            assert memory_fs.read_text("/ctx/" + e.patch).startswith("diff --git a/%s " % e.file), e
            assert (e.module, e.truncated) == ("src/a", False)
        counts = {e.file: (e.added, e.removed) for e in index}
        assert counts == {"src/a/keep.py": (1, 1), "src/a/old.py": (0, 30), 'src/a/we"ird name.py': (1, 0)}
        assert index[1].hunks == []  # old.py is removed: none of its lines is left to comment on
        assert DiffIndex.model_validate_json(memory_fs.read_text("/ctx/diff/index.json")).root == index


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
                    "files": {lf("policy_file"): '{"unrated_band": "High", "business_weights": {"dependents": 0.4}}'},
                }
            ]
        ],
        indirect=True,
    )
    def test_valid_overrides_at_the_base_are_applied(
        self,
        git,
        shas,
        policy_file,
    ):
        policy, source, raw, note = policy_at(git, shas[0])
        assert (policy.unrated_band, policy.business_weights["dependents"]) == ("High", 0.4)
        assert (source, raw, note) == (policy_file + "@" + shas[0][:8], git.show(shas[0], policy_file), None)

    @pytest.mark.parametrize(
        "history",
        [
            [
                {"subject": "base", "files": {"a.py": "x = 1\n"}},
                {"subject": "weaken", "files": {lf("policy_file"): '{"unrated_band": "Low"}'}},
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

    @pytest.mark.parametrize(
        "git",
        [
            {
                "show.side_effect": HeatmapError(
                    "git show failed",
                )
            }
        ],
        indirect=True,
    )
    def test_a_git_failure_is_not_mistaken_for_no_policy(
        self,
        git,
    ):
        with pytest.raises(HeatmapError):
            policy_at(git, "0" * 40)

    @pytest.mark.parametrize(
        ("history", "expect"),
        [
            ([{"subject": "base", "files": {lf("policy_file"): '{"window_days": 30}'}}], "v1 model"),
            ([{"subject": "base", "files": {lf("policy_file"): '{"effects_scale": 0}'}}], "greater than 0"),
            ([{"subject": "base", "files": {lf("policy_file"): "{not json"}}], "not valid JSON"),
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


class TestRunReviewAction:
    """The review's CI run refuses to run without a PR number before it touches the code host or the agent. (The
    settings refuse an unknown fail-on: tests/test_settings.py.)"""

    def test_a_run_without_a_pr_number_is_refused_first(
        self,
        host,
        git,
        fs,
        clock,
        env,
        agent,
        runner,
        run_log,
        settings,
        run_folders,
    ):
        with pytest.raises(RunFailed, match="needs a PR number"):
            run_review_action(host, git, fs, clock, env, agent, runner, run_log, run_folders, settings, "")
        host.pull_request.assert_not_called()
        agent.run.assert_not_called()
