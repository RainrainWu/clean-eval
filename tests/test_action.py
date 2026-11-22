"""End-to-end tests of the GitHub Action's command, `clean_eval action` (what action.yml runs), with fake `claude` and
`gh` executables (no network, no tokens). The fixtures build the workspace the action runs on, tell the fakes what to
do, and run it: `action_run` once with the inputs it is parametrized with, `action.run` again from a test
(tests.action.Action). What they are parametrized with (the workspaces' commits, the pull request, the fakes' replies
and plans) are fixtures too, in tests/conftest.py."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from tests.action import GH_TOKEN
from tests.helpers import ROOT

pytestmark = pytest.mark.e2e  # every test runs the action's command in a subprocess


class TestInputsAndCheckout:
    """The action refuses bad inputs and a shallow checkout before it runs anything."""

    @pytest.mark.parametrize(
        ("action_run", "error"),
        [
            ({"skill": "nope"}, "'nope' is not one of"),
            (
                {"skill": "pr-review", "pr": "1; rm -rf /"},
                "invalid settings: review.CE_PR: Input should be a valid integer",
            ),
            (
                {"skill": "eval", "since": "yesterday"},
                "invalid settings: evaluation.CE_SINCE: Input should be a valid date",
            ),
            (
                {"skill": "pr-review", "pr": "7", "fail_on": "sometimes"},
                "invalid settings: review.CE_FAIL_ON: Input should be",
            ),
        ],
        indirect=["action_run"],
        ids=["skill", "pr", "since", "fail-on"],
    )
    def test_a_bad_input_is_refused_before_anything_runs(
        self,
        action_run,
        error,
        fake_bin,
    ):
        assert error in action_run.stderr
        assert fake_bin.gh_calls() == []  # rejected before anything ran

    def test_a_shallow_clone_is_refused(
        self,
        action,
        shallow_clone,
    ):
        result = action.run(shallow_clone, skill="risk-heat-map")
        assert result.returncode != 0
        assert "shallow clone" in result.stderr


class TestRiskHeatMap:
    """risk-heat-map without the agent: the README's section and its diagram, changed only when the map changes."""

    @pytest.mark.parametrize("workspace", [lf("two_modules_workspace")], indirect=True)
    def test_the_readme_changes_only_on_a_real_change(
        self,
        action_run,
        action,
        workspace,
        fake_bin,
    ):
        assert action_run.returncode == 0, action_run.stderr
        assert action.outputs()["readme-changed"] == "true"  # README and diagram created
        readme = (
            Path(
                workspace.path,
            )
            / "README.md"
        ).read_text()
        assert "## Risk Heat Map" in readme
        assert "](docs/risk-heatmap.svg)" in readme
        assert (
            Path(
                workspace.path,
            )
            / "docs"
            / "risk-heatmap.svg"
        ).exists()
        assert "`src" in action.summary.read_text()
        assert "page" not in action.outputs()
        assert (
            json.loads(
                Path(
                    action.outputs()["result"],
                ).read_text()
            )["repo"]
            == Path(
                workspace.path,
            ).name
        )
        workspace.commit("docs: heat map")
        action.run(skill="risk-heat-map")
        assert action.outputs()["readme-changed"] == "false"
        assert fake_bin.claude_invocation() is None  # no agent unless use-claude


@pytest.mark.parametrize("action_run", [{"skill": "risk-heat-map", "use_claude": "true"}], indirect=True)
class TestRiskHeatMapWithClaude:
    """risk-heat-map with use-claude: the sandboxed agent levels the aspects; its levels are validated and its reasons
    redacted, and without a valid judgement.json the static levels are published."""

    @pytest.mark.parametrize("workspace", [lf("billing_and_users_workspace")], indirect=True)
    @pytest.mark.parametrize("claude_plan", [lf("levels_plan")], indirect=True)
    def test_the_agents_levels_are_applied(
        self,
        action_run,
        action,
        fake_bin,
    ):
        assert action_run.returncode == 0, action_run.stderr
        invocation = fake_bin.claude_invocation()
        for flag in ("--restricted", "--strict-mcp-config", "--settings"):
            assert flag in invocation["argv"]
        assert "GH_TOKEN" not in invocation["env"]
        final = json.loads(
            Path(
                action.outputs()["result"],
            ).read_text()
        )
        src = [module for module in final["modules"] if module["name"] == "src"][0]
        assert final["judged_modules"] == 1
        assert src["scores"]["biz"] >= 75  # level 0 -> Critical band
        assert src["scores"]["unverified"] == src["baseline"]["unverified"]  # invalid level ignored
        assert GH_TOKEN not in json.dumps(final)  # redacted before use
        assert "[REDACTED]" in src["evidence"]["biz"][0]
        assert action.outputs()["readme-changed"] == "true"

    @pytest.mark.parametrize("workspace", [lf("one_function_workspace")], indirect=True)
    @pytest.mark.parametrize(
        ("claude_plan", "warning"),
        [
            ({"agent_files": {"judgement.json": "{not json"}}, "not a JSON object"),
            ({"agent_files": {"judgement.json": [1, 2]}}, "not a JSON object"),
            ({}, "wrote no judgement.json"),
        ],
        indirect=["claude_plan"],
        ids=["not JSON", "not an object", "none"],
    )
    def test_without_a_valid_judgement_it_falls_back_to_the_static_levels(
        self,
        action_run,
        action,
        warning,
    ):
        assert action_run.returncode == 0, action_run.stderr
        assert warning in action_run.stderr
        assert (
            json.loads(
                Path(
                    action.outputs()["result"],
                ).read_text()
            )["judged_modules"]
            == 0
        )


@pytest.mark.parametrize("workspace", [lf("discounts_workspace")], indirect=True)
@pytest.mark.parametrize("pull_request", [lf("discounts_pull_request")], indirect=True)
@pytest.mark.parametrize("gh_replies", [lf("posting_replies")], indirect=True)
@pytest.mark.parametrize("claude_plan", [lf("review_plan")], indirect=True)
@pytest.mark.parametrize("action_run", [{"skill": "pr-review", "pr": "7", "fail_on": "blocker"}], indirect=True)
class TestPrReviewEndToEnd:
    """pr-review of PR #7, whose review the action floors to a blocker: the agent reviews it sandboxed, from a context
    precomputed at the base, and the action validates the review, derives the verdict, posts it and fails the run."""

    def test_the_run_fails_on_the_blocker_with_the_verdict_it_derives(
        self,
        action_run,
        action,
    ):
        assert action_run.returncode == 1  # CA-01 floored to blocker → fail-on=blocker
        assert "1 blocker finding" in action_run.stderr
        outputs = action.outputs()
        risk = json.loads((action.out / "context" / "risk.json").read_text())
        assert (outputs["verdict"], outputs["blockers"], outputs["max-tier"]) == (
            "request_changes",
            "1",
            risk["max_tier"],
        )
        assert risk["max_tier"] in ("Low", "Moderate", "High", "Critical")
        assert risk["files"]["src/new/n.py"] == "src"  # a new file inside a rated module takes its band
        assert "evidence" in risk["modules"][0]

    def test_the_agent_runs_sandboxed(
        self,
        action_run,
        workspace,
        fake_bin,
    ):
        # restricted mode, our settings, read tools only, no GitHub or runner token, no stdin, in the checkout
        invocation = fake_bin.claude_invocation()
        for flag in ("--restricted", "--strict-mcp-config", "--settings", "--add-dir"):
            assert flag in invocation["argv"]
        assert "Bash" not in " ".join(invocation["argv"][invocation["argv"].index("--allowedTools") :])
        assert "GH_TOKEN" not in invocation["env"]
        assert "SOME_RUNNER_TOKEN" not in invocation["env"]
        assert "ANTHROPIC_API_KEY" in invocation["env"]
        assert "CE_GUARD_WRITE_ROOTS" in invocation["env"]
        assert invocation["stdin"] == ""
        assert os.path.realpath(invocation["cwd"]) == os.path.realpath(workspace.path)

    def test_its_guard_hook_allows_the_checkout_denies_the_rest_and_denies_when_it_fails(
        self,
        action_run,
        action,
        workspace,
        crashing_package,
    ):
        # the hook, run by a shell as Claude Code runs it
        settings = json.loads((action.out / "context" / "claude-settings.json").read_text())
        hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        inside = os.path.join(workspace.path, "src", "billing", "a.py")
        assert hook.endswith(" guard || exit 2"), hook
        assert action.run_hook(hook, "Read", file_path=inside).returncode == 0
        assert action.run_hook(hook, "Bash", command="env").returncode == 2
        assert action.run_hook(hook, "Read", file_path="/etc/hosts").returncode == 2
        package = os.path.join(os.path.realpath(ROOT), "clean_eval")
        assert package in hook
        broken = hook.replace(package, str(crashing_package))  # a hook that dies with status 1 must still deny
        assert subprocess.run([sys.executable, str(crashing_package)], capture_output=True).returncode == 1
        assert action.run_hook(broken, "Read", file_path=inside).returncode == 2

    def test_the_agent_takes_no_instructions_from_the_checkout(
        self,
        action_run,
        action,
        fake_bin,
    ):
        # no memory files, AGENTS.md, rules or skills
        assert "CLAUDE_CODE_DISABLE_CLAUDE_MDS" in fake_bin.claude_invocation()["env"]
        settings = json.loads((action.out / "context" / "claude-settings.json").read_text())
        assert {"**/CLAUDE.md", "**/CLAUDE.local.md", "**/AGENTS.md", "**/.claude/rules/**"} <= set(
            settings["claudeMdExcludes"]
        )
        agents_md = settings["pluginConfigs"]["cc-plugin-agents-md@builtin"]["options"]
        assert agents_md["instructionFiles"] == "claude-md"
        assert settings["permissions"]["deny"] == ["Skill"]

    def test_the_context_is_precomputed_from_the_base(
        self,
        action_run,
        action,
        workspace,
    ):
        # risk at the base, the diff, the rules from the base
        context = action.out / "context"
        prepared = json.loads((context / "context.json").read_text())
        assert (prepared["base"], prepared["head"]) == (
            workspace.git("rev-parse", "main"),
            workspace.git("rev-parse", "feature"),
        )
        index = json.loads((context / "diff" / "index.json").read_text())
        assert [entry["file"] for entry in index] == ["src/billing/a.py", "src/new/n.py"]
        assert prepared["diff_patches"] == 2
        assert "+n" in (context / index[1]["patch"]).read_text()

    def test_one_review_is_posted_with_the_derived_verdict(
        self,
        action_run,
        action,
        workspace,
        fake_bin,
    ):
        # one review, the derived verdict, our marker only, the secret redacted
        risk = json.loads((action.out / "context" / "risk.json").read_text())
        post = [json.loads(call["stdin"]) for call in fake_bin.gh_calls() if "POST" in call["args"]][0]
        assert post["event"] == "REQUEST_CHANGES"
        assert post["commit_id"] == workspace.git("rev-parse", "feature")
        assert GH_TOKEN not in post["body"]
        assert post["body"].count("clean-eval:pr-review") == 1
        assert "tier=%s" % risk["max_tier"] in post["body"]
        assert len(post["comments"]) == 1
        final = json.loads((action.out / "result.json").read_text())
        assert [finding["rule"] for finding in final["findings"]] == ["CA-01", "NEW"]


class TestPrReview:
    """pr-review of PR #7: an invalid review is never posted."""

    @pytest.mark.parametrize("claude_plan", [lf("invalid_review_plan")], indirect=True)
    @pytest.mark.parametrize("action_run", [{"skill": "pr-review", "pr": "7", "fail_on": "never"}], indirect=True)
    def test_an_invalid_review_is_not_posted(
        self,
        action_run,
        fake_bin,
    ):
        assert action_run.returncode == 0
        assert not any("POST" in call["args"] for call in fake_bin.gh_calls())

    @pytest.mark.parametrize("claude_plan", [lf("invalid_review_plan")], indirect=True)
    @pytest.mark.parametrize("action_run", [{"skill": "pr-review", "pr": "7", "fail_on": "blocker"}], indirect=True)
    def test_an_invalid_review_fails_closed_when_findings_can_fail_the_run(
        self,
        action_run,
    ):
        assert action_run.returncode == 1
        assert "failing closed" in action_run.stderr


@pytest.mark.parametrize("claude_plan", [lf("no_findings_plan")], indirect=True)
class TestPrReviewJudgesByTheBase:
    """pr-review takes its configuration from the PR's base commit, so a PR can't relax the rules it is judged by."""

    @pytest.mark.parametrize("workspace", [lf("v1_policy_at_base_workspace")], indirect=True)
    @pytest.mark.parametrize("pull_request", [{"number": 4}], indirect=True)
    @pytest.mark.parametrize("action_run", [{"skill": "pr-review", "pr": "4", "post": "false"}], indirect=True)
    def test_an_invalid_base_policy_falls_back_with_a_note(
        self,
        action_run,
        action,
    ):
        assert action_run.returncode == 0, action_run.stderr
        risk = json.loads((action.out / "context" / "risk.json").read_text())
        assert risk["model_source"] == "clean-eval default"
        assert any(
            "ignored .clean-eval/risk-model.json at the base" in note and "v1 model" in note for note in risk["notes"]
        )

    @pytest.mark.parametrize("workspace", [lf("rules_weakened_workspace")], indirect=True)
    @pytest.mark.parametrize("pull_request", [{"number": 3}], indirect=True)
    @pytest.mark.parametrize("gh_replies", [lf("posting_replies")], indirect=True)
    @pytest.mark.parametrize("action_run", [{"skill": "pr-review", "pr": "3", "post": "false"}], indirect=True)
    def test_the_review_rules_come_from_the_base(
        self,
        action_run,
        action,
        fake_bin,
    ):
        assert action_run.returncode == 0
        rules = (action.out / "context" / "review-rules.md").read_text()
        assert "base rule" in rules
        assert "nothing to see" not in rules
        assert not any("POST" in call["args"] for call in fake_bin.gh_calls())  # post=false


@pytest.mark.parametrize("workspace", [lf("billing_change_workspace")], indirect=True)
@pytest.mark.parametrize("pull_request", [{"number": 9}], indirect=True)
@pytest.mark.parametrize("gh_replies", [lf("not_logged_in_replies")], indirect=True)
@pytest.mark.parametrize("claude_plan", [lf("every_output_plan")], indirect=True)
class TestModulesPlantedInTheCheckout:
    """A PR can add json.py and friends at its root; with every Python run isolated, none of them is imported."""

    @pytest.mark.parametrize(
        "inputs",
        [
            {"skill": "pr-review", "pr": "9", "post": "false"},
            {"skill": "risk-heat-map", "use_claude": "true"},
            {"skill": "eval"},
        ],
        ids=["pr-review", "risk-heat-map", "eval"],
    )
    def test_never_run(
        self,
        action,
        planted_modules,
        inputs,
    ):
        result = action.run(**inputs)
        assert result.returncode == 0, (inputs, result.stderr)
        assert not planted_modules.exists(), planted_modules.read_text() if planted_modules.exists() else ""


@pytest.mark.parametrize("workspace", [lf("feature_and_fix_workspace")], indirect=True)
@pytest.mark.parametrize("gh_replies", [lf("not_logged_in_replies")], indirect=True)
class TestEval:
    """eval: the agent may edit .clean-eval/ only with apply, and only through a real folder; the action reverts an
    edit it doesn't accept."""

    @pytest.mark.parametrize(  # an edit of the risk model that changes what is measured: not allowed in CI
        "claude_plan",
        [
            lfc(
                lambda plan: {**plan, "workspace_files": {".clean-eval/risk-model.json": {"depth": 1}}},
                lf("eval_report_plan"),
            )
        ],
        indirect=True,
        ids=["structural edit"],
    )
    @pytest.mark.parametrize(
        "action_run", [{"skill": "eval", "since": lf("eval_since"), "apply": "true"}], indirect=True
    )
    def test_a_disallowed_edit_is_reverted(
        self,
        action_run,
        action,
        workspace,
        fake_bin,
    ):
        assert action_run.returncode == 0, action_run.stderr
        final = json.loads((action.out / "result.json").read_text())
        assert not final["model_change"]["accepted"]
        assert not (
            Path(
                workspace.path,
            )
            / ".clean-eval"
            / "risk-model.json"
        ).exists()
        assert action.outputs()["applied"] == "false"
        assert final["prs"] == 2
        invocation = fake_bin.claude_invocation()
        assert (
            "Edit(/%s/.clean-eval/**)"
            % Path(
                workspace.path,
            ).resolve()
            in invocation["argv"]
        )
        for name in ("eval-history.json", "sensitivity.json", "defects/index.json"):
            assert (action.out / "context" / name).exists(), name

    @pytest.mark.parametrize("workspace_links", [{".clean-eval": ".."}], indirect=True)
    @pytest.mark.parametrize(
        "action_run", [{"skill": "eval", "since": lf("eval_since"), "apply": "true"}], indirect=True
    )
    def test_a_symlinked_clean_eval_is_refused(
        self,
        action_run,
        fake_bin,
    ):
        assert action_run.returncode != 0
        assert "symlink" in action_run.stderr
        assert fake_bin.claude_invocation() is None

    @pytest.mark.parametrize("claude_plan", [lf("eval_report_plan")], indirect=True)
    @pytest.mark.parametrize("action_run", [{"skill": "eval", "since": lf("eval_since")}], indirect=True)
    def test_without_apply_the_agent_gets_no_write_access_to_the_workspace(
        self,
        action_run,
        fake_bin,
    ):
        assert action_run.returncode == 0, action_run.stderr
        argv = fake_bin.claude_invocation()["argv"]
        assert not any(".clean-eval" in arg for arg in argv if arg.startswith("Edit("))
