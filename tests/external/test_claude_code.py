"""The Claude Code gateway (external/claude_code.py), with what it calls out to mocked (`claude_runs`, `npm_installs`,
`claude_installed`, `oidc_tokens`): installing the CLI only when it is missing, refusing to run without credentials in
CI, the sandbox it runs the agent in, and keyless sign-in by workload identity federation, which trades the job's OIDC
token. `tests/test_action.py` runs it with a fake `claude` executable, end to end."""

import json
import os
import re
import subprocess
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import call

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.review import AgentRun
from clean_eval.external.claude_code import OIDC_REFRESH_SECONDS, VERSION, github_oidc_token

# A CI runner's variables: without credentials, and with them.
CI_WITHOUT_CREDENTIALS = {"PATH": "/usr/bin", "GITHUB_ACTIONS": "true"}
CI_WITH_A_KEY = {**CI_WITHOUT_CREDENTIALS, "ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 40}
# A CI job that signs in keyless, by workload identity federation: the rule, the organization, and what the runner sets
# for a job with `id-token: write` to request its OIDC token.
TOKEN_REQUEST_URL = "https://token.actions.example/idtoken?api-version=2.0"
TOKEN_REQUEST_TOKEN = "request-" + "r" * 32
FEDERATED_JOB = {
    **CI_WITHOUT_CREDENTIALS,
    "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_example",
    "ANTHROPIC_ORGANIZATION_ID": "00000000-0000-4000-8000-000000000000",
    "ACTIONS_ID_TOKEN_REQUEST_URL": TOKEN_REQUEST_URL,
    "ACTIONS_ID_TOKEN_REQUEST_TOKEN": TOKEN_REQUEST_TOKEN,
}
NO_TOKEN_REQUEST = {
    name: value for name, value in FEDERATED_JOB.items() if not name.startswith("ACTIONS_ID_TOKEN_REQUEST_")
}
# Federation set up wrong: the variables, and what require() says.
FEDERATION_REFUSED = [
    (
        {name: value for name, value in FEDERATED_JOB.items() if name != "ANTHROPIC_ORGANIZATION_ID"},
        "needs the organization's ID",
    ),
    (NO_TOKEN_REQUEST, "give the job `id-token: write`"),
    (
        {**FEDERATED_JOB, "ACTIONS_ID_TOKEN_REQUEST_TOKEN": ""},
        "give the job `id-token: write`",
    ),
    (
        {**FEDERATED_JOB, "ANTHROPIC_API_KEY": "sk-ant-api03-" + "k" * 40},
        "and so is ANTHROPIC_API_KEY, which Claude Code would sign in with instead",
    ),
    (
        {**FEDERATED_JOB, "CLAUDE_CODE_USE_BEDROCK": "1"},
        "and so is CLAUDE_CODE_USE_BEDROCK",
    ),
]
# What GitHub's token endpoint may answer instead of a token.
BAD_TOKEN_REPLIES = [
    urllib.error.URLError("timed out"),
    urllib.error.HTTPError(TOKEN_REQUEST_URL, 403, "Forbidden", Message(), None),
    b"<html>not JSON</html>",
    b"[]",
    b'{"count": 0}',
]


class TestRequire:
    """Claude Code is installed with npm, away from the checkout, only when it is missing; in CI it needs credentials."""

    def test_an_installed_claude_is_used_as_it_is(
        self,
        claude_code,
        npm_installs,
    ):
        claude_code.require()
        npm_installs.assert_not_called()

    @pytest.mark.parametrize("claude_installed", [False], indirect=True)
    def test_a_missing_claude_is_installed_from_the_output_folder(
        self,
        claude_code,
        npm_installs,
        run_folders,
    ):
        claude_code.require()
        command = npm_installs.call_args.args[0]
        assert command == ["npm", "install", "-g", "@anthropic-ai/claude-code@%s" % VERSION]
        assert npm_installs.call_args.kwargs["cwd"] == run_folders.out  # not the checkout: its .npmrc is untrusted

    @pytest.mark.parametrize("claude_installed", [False], indirect=True)
    @pytest.mark.parametrize("npm_installs", [{"exit": 1, "err": "npm ERR! 404"}], indirect=True)
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

    @pytest.mark.parametrize(
        "claude_code",
        [{"variables": CI_WITH_A_KEY}, {"variables": FEDERATED_JOB}],
        indirect=True,
        ids=["key", "keyless"],
    )
    def test_in_ci_an_api_key_or_federation_will_do(
        self,
        claude_code,
    ):
        claude_code.require()


@pytest.mark.parametrize("claude_installed", [False], indirect=True)
class TestRequireWithFederation:
    """Federation needs the organization and the job's OIDC token, and no credential Claude Code would sign in with
    instead; set up wrong, it is refused before anything is installed."""

    @pytest.mark.parametrize(
        ("claude_code", "refusal"),
        [({"variables": variables}, refusal) for variables, refusal in FEDERATION_REFUSED],
        indirect=["claude_code"],
        ids=["no organization", "no id-token permission", "no request token", "an API key too", "Bedrock too"],
    )
    def test_a_wrong_setup_is_refused(
        self,
        claude_code,
        refusal,
        npm_installs,
    ):
        with pytest.raises(HeatmapError, match=re.escape(refusal)):
            claude_code.require()
        npm_installs.assert_not_called()

    @pytest.mark.parametrize("claude_code", [{"variables": FEDERATED_JOB}], indirect=True)
    def test_a_complete_setup_goes_on_to_install(
        self,
        claude_code,
        npm_installs,
        oidc_tokens,
    ):
        claude_code.require()
        npm_installs.assert_called_once()
        oidc_tokens.assert_not_called()  # the job's token is fetched only when the agent runs


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
    def test_it_waits_for_claude_and_returns_its_status_and_transcript(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []) == AgentRun(status=3, transcript="reviewed, then failed")
        claude_runs.return_value.communicate.assert_called_once_with(timeout=None)

    @pytest.mark.parametrize("claude_runs", [FileNotFoundError("claude")], indirect=True)
    def test_a_claude_that_cannot_start_is_a_failed_run(
        self,
        claude_code,
        run_folders,
    ):
        run = claude_code.run("Review it.", run_folders, [])
        assert run.status == 127
        assert "could not run claude" in run.transcript


@pytest.mark.parametrize("claude_code", [{"variables": FEDERATED_JOB}], indirect=True)
class TestRunWithFederation:
    """With federation, claude reads the job's OIDC token from a file outside every folder it can reach; a new token is
    written while it runs, since each lasts minutes and serves one trade; and the file is gone once it stops."""

    def test_claude_reads_the_jobs_token_from_a_file_out_of_its_reach(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []) == AgentRun(status=0, transcript="done")
        environment = claude_runs.call_args.kwargs["env"]
        token = environment["ANTHROPIC_IDENTITY_TOKEN_FILE"]
        assert token == str(Path(run_folders.out, "identity", "token"))
        for reachable in (run_folders.workspace, run_folders.context, run_folders.agent):
            assert not token.startswith(reachable + os.sep)
        assert claude_runs.identity_tokens == ["jwt-1"]
        assert not Path(token).exists()

    def test_claude_gets_the_federation_settings_but_not_the_means_to_mint_tokens(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        claude_code.run("Review it.", run_folders, [])
        environment = claude_runs.call_args.kwargs["env"]
        assert environment["ANTHROPIC_FEDERATION_RULE_ID"] == "fdrl_example"
        assert environment["ANTHROPIC_ORGANIZATION_ID"] == FEDERATED_JOB["ANTHROPIC_ORGANIZATION_ID"]
        assert not {"ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "GITHUB_ACTIONS"} & set(
            environment
        )

    def test_the_token_is_requested_for_anthropic_with_the_jobs_request_token(
        self,
        claude_code,
        oidc_tokens,
        run_folders,
    ):
        claude_code.run("Review it.", run_folders, [])
        request = oidc_tokens.call_args.args[0]
        assert request.full_url == TOKEN_REQUEST_URL + "&audience=https%3A%2F%2Fapi.anthropic.com"
        assert request.get_header("Authorization") == "bearer " + TOKEN_REQUEST_TOKEN

    @pytest.mark.parametrize("claude_runs", [{"timeouts": 2, "out": "reviewed"}], indirect=True)
    def test_a_new_token_is_written_while_claude_runs(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []) == AgentRun(status=0, transcript="reviewed")
        assert claude_runs.identity_tokens == ["jwt-1", "jwt-2", "jwt-3"]
        assert claude_runs.return_value.communicate.call_args_list == [call(timeout=OIDC_REFRESH_SECONDS)] * 3

    @pytest.mark.parametrize("claude_runs", [{"timeouts": 2}], indirect=True)
    @pytest.mark.parametrize("oidc_tokens", [["jwt-1", urllib.error.URLError("timed out")]], indirect=True)
    def test_a_failed_refresh_lets_claude_run_on(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []) == AgentRun(status=0, transcript="done")
        assert claude_runs.identity_tokens == ["jwt-1", "jwt-1", "jwt-3"]

    @pytest.mark.parametrize("oidc_tokens", [[urllib.error.URLError("timed out")]], indirect=True)
    def test_without_the_jobs_token_claude_does_not_start(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        run = claude_code.run("Review it.", run_folders, [])
        assert run.status == 1
        assert run.transcript.startswith("could not sign in to Claude: could not get the job's OIDC token")
        claude_runs.assert_not_called()
        assert not Path(run_folders.out, "identity", "token").exists()

    @pytest.mark.parametrize("run_folders", ["ws/out"], indirect=True)
    def test_an_output_folder_inside_the_checkout_is_refused(
        self,
        claude_code,
        claude_runs,
        oidc_tokens,
        run_folders,
    ):
        with pytest.raises(HeatmapError, match="an output folder outside the checkout"):
            claude_code.run("Review it.", run_folders, [])
        oidc_tokens.assert_not_called()
        claude_runs.assert_not_called()

    @pytest.mark.parametrize("claude_runs", [FileNotFoundError("claude")], indirect=True)
    def test_a_claude_that_cannot_start_leaves_no_token(
        self,
        claude_code,
        run_folders,
    ):
        assert claude_code.run("Review it.", run_folders, []).status == 127
        assert not Path(run_folders.out, "identity", "token").exists()

    @pytest.mark.parametrize("claude_runs", [{"timeouts": 1, "interrupted": KeyboardInterrupt()}], indirect=True)
    def test_a_run_cut_short_stops_claude_and_leaves_no_token(
        self,
        claude_code,
        claude_runs,
        run_folders,
    ):
        with pytest.raises(KeyboardInterrupt):
            claude_code.run("Review it.", run_folders, [])
        claude_runs.return_value.kill.assert_called_once_with()
        assert not Path(run_folders.out, "identity", "token").exists()


class TestGithubOidcToken:
    """The job's OIDC token, from the endpoint the runner names; anything else is a HeatmapError."""

    def test_the_token_is_the_value_github_issues(
        self,
        oidc_tokens,
    ):
        assert github_oidc_token(FEDERATED_JOB, "https://audience.example") == "jwt-1"
        assert oidc_tokens.call_args.args[0].full_url == TOKEN_REQUEST_URL + "&audience=https%3A%2F%2Faudience.example"
        assert oidc_tokens.call_args.kwargs["timeout"] == 30

    def test_a_job_without_id_token_permission_has_none(
        self,
        oidc_tokens,
    ):
        with pytest.raises(HeatmapError, match="give the job `id-token: write`"):
            github_oidc_token(NO_TOKEN_REQUEST)
        oidc_tokens.assert_not_called()

    @pytest.mark.parametrize(
        "oidc_tokens",
        [[reply] for reply in BAD_TOKEN_REPLIES],
        indirect=True,
        ids=["no answer", "refused", "not JSON", "not an object", "no value"],
    )
    def test_anything_but_a_token_is_an_error(
        self,
        oidc_tokens,
    ):
        with pytest.raises(HeatmapError, match="could not get the job's OIDC token"):
            github_oidc_token(FEDERATED_JOB)
