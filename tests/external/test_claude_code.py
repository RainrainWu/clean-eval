"""The Claude Code gateway (external/claude_code.py), with its CLI and npm mocked (`claude_runs`, `claude_installed`):
installing it only when it is missing, refusing to run without credentials in CI, and the sandbox it runs the agent in.
`tests/test_action.py` runs it with a fake `claude` executable, end to end."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.review import AgentRun
from clean_eval.external.claude_code import VERSION

# A CI runner's variables: without credentials, and with them.
CI_WITHOUT_CREDENTIALS = {"PATH": "/usr/bin", "GITHUB_ACTIONS": "true"}
CI_WITH_A_KEY = {**CI_WITHOUT_CREDENTIALS, "ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 40}


class TestRequire:
    """Claude Code is installed with npm, away from the checkout, only when it is missing; in CI it needs credentials."""

    def test_an_installed_claude_is_used_as_it_is(
        self,
        claude_code,
        claude_runs,
    ):
        claude_code.require()
        claude_runs.assert_not_called()

    @pytest.mark.parametrize("claude_installed", [False], indirect=True)
    def test_a_missing_claude_is_installed_from_the_output_folder(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        claude_code.require()
        command = claude_runs.call_args.args[0]
        assert command == ["npm", "install", "-g", "@anthropic-ai/claude-code@%s" % VERSION]
        assert claude_runs.call_args.kwargs["cwd"] == run_folders.out  # not the checkout: its .npmrc is untrusted

    @pytest.mark.parametrize("claude_installed", [False], indirect=True)
    @pytest.mark.parametrize("claude_runs", [{"exit": 1, "err": "npm ERR! 404"}], indirect=True)
    def test_a_failed_install_is_reported(
        self,
        claude_code,
    ):
        with pytest.raises(HeatmapError, match="could not install Claude Code .*404"):
            claude_code.require()

    @pytest.mark.parametrize("claude_code", [{"variables": CI_WITHOUT_CREDENTIALS}], indirect=True)
    def test_in_ci_it_needs_credentials(
        self,
        claude_code,
    ):
        with pytest.raises(HeatmapError, match="no Claude credentials"):
            claude_code.require()

    @pytest.mark.parametrize("claude_code", [{"variables": CI_WITH_A_KEY}], indirect=True)
    def test_in_ci_an_api_key_will_do(
        self,
        claude_code,
    ):
        claude_code.require()


class TestRun:
    """The agent runs sandboxed: restricted, with the settings, the hook, the tools and the environment it may have."""

    def test_its_settings_take_no_instructions_from_the_checkout_and_hook_the_guard(
        self,
        claude_code,
        run_folders,
    ):
        claude_code.run("Review it.", run_folders, [])
        settings = json.loads(Path(run_folders.context, "claude-settings.json").read_text())
        assert settings["hooks"]["PreToolUse"] == [
            {"matcher": "*", "hooks": [{"type": "command", "command": "python -I /action/clean_eval guard || exit 2"}]}
        ]
        assert settings["claudeMdExcludes"] == [
            "**/CLAUDE.md",
            "**/CLAUDE.local.md",
            "**/AGENTS.md",
            "**/.claude/rules/**",
        ]
        assert settings["pluginConfigs"]["cc-plugin-agents-md@builtin"]["options"]["instructionFiles"] == "claude-md"
        assert settings["permissions"]["deny"] == ["Skill"]

    def test_it_runs_restricted_with_read_tools_and_edits_only_where_it_may_write(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        tuning = run_folders.workspace + "/.clean-eval"
        claude_code.run("Review it.", run_folders, [tuning])
        command = claude_runs.call_args.args[0]
        assert command[:3] == ["claude", "-p", "Review it."]
        for flag in ("--restricted", "--strict-mcp-config", "--settings", "--append-system-prompt", "--max-turns"):
            assert flag in command
        assert command[command.index("--add-dir") + 1 : command.index("--allowedTools")] == [
            run_folders.context,
            run_folders.agent,
        ]
        assert command[command.index("--allowedTools") + 1 :] == [
            "Read",
            "Grep",
            "Glob",
            "Edit(/%s/**)" % run_folders.agent,
            "Edit(/%s/**)" % tuning,
        ]
        assert "--model" not in command
        assert claude_runs.call_args.kwargs["stdin"] == subprocess.DEVNULL
        assert claude_runs.call_args.kwargs["cwd"] == run_folders.workspace

    def test_its_environment_keeps_only_what_it_needs(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        claude_code.run("Review it.", run_folders, [])
        environment = claude_runs.call_args.kwargs["env"]
        assert set(environment) == {
            "PATH",
            "ANTHROPIC_API_KEY",
            "CE_GUARD_READ_ROOTS",
            "CE_GUARD_WRITE_ROOTS",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS",
        }
        assert environment["CE_GUARD_READ_ROOTS"] == os.pathsep.join([run_folders.workspace, run_folders.context])
        assert environment["CE_GUARD_WRITE_ROOTS"] == run_folders.agent
        assert environment["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"

    @pytest.mark.parametrize("claude_code", [{"model": "claude-opus-5-5", "max_turns": 9}], indirect=True)
    def test_a_model_and_a_turn_limit_are_passed_on(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        claude_code.run("Review it.", run_folders, [])
        command = claude_runs.call_args.args[0]
        assert command[command.index("--model") + 1] == "claude-opus-5-5"
        assert command[command.index("--max-turns") + 1] == "9"

    @pytest.mark.parametrize("claude_runs", [{"exit": 3, "out": "reviewed, then failed"}], indirect=True)
    def test_its_status_and_transcript_are_returned(
        self,
        claude_code,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []) == AgentRun(status=3, transcript="reviewed, then failed")

    @pytest.mark.parametrize("claude_runs", [FileNotFoundError("claude")], indirect=True)
    def test_a_claude_that_cannot_start_is_a_failed_run(
        self,
        claude_code,
        run_folders,
    ):
        run = claude_code.run("Review it.", run_folders, [])
        assert run.status == 127
        assert "could not run claude" in run.transcript
