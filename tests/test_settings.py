"""A run's settings (clean_eval/settings.py), read from the environment as the command line reads them (`settings`, or
`settings_environment` and Settings() for one that must be refused): each setting from its variable, typed; an empty
variable as unset; nothing from files; secrets kept hidden; and action.yml's inputs, all of them settings."""

import datetime as dt
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from clean_eval.settings import (
    CLAUDE_CODE_VERSION,
    Settings,
)


class TestFromTheEnvironment:
    """Each setting reads its variable, typed as the run uses it, or takes its default."""

    @pytest.mark.parametrize(
        "environ",
        [lfc(lambda review, evaluation: {**review, **evaluation}, lf("review_run"), lf("eval_run"))],
        indirect=True,
    )
    def test_each_setting_reads_its_variable(
        self,
        settings,
    ):
        assert (settings.run.workspace, settings.run.out) == ("/runner/work/repo", "/runner/temp/clean-eval")
        assert (settings.review.pr, settings.review.post, settings.review.fail_on) == (7, False, "blocker")
        assert (settings.evaluation.since, settings.evaluation.until, settings.evaluation.apply) == (
            dt.date(2026, 1, 1),
            dt.date(2026, 2, 1),
            True,
        )
        assert (settings.agent.model, settings.agent.max_turns) == ("claude-opus-5-5", 9)
        assert (settings.github.step_outputs, settings.github.step_summary) == ("/runner/outputs", "/runner/summary.md")
        assert settings.github.in_actions

    def test_without_its_variable_a_setting_has_its_default(
        self,
        settings,
    ):
        assert (settings.run.workspace, settings.run.out, settings.review.pr, settings.review.post) == (
            ".",
            "/tmp/clean-eval",
            None,
            True,
        )
        assert (
            settings.review.fail_on,
            settings.evaluation.since,
            settings.evaluation.apply,
            settings.heatmap.use_claude,
        ) == ("never", None, False, False)
        assert (settings.agent.claude_code_version, settings.agent.max_turns) == (CLAUDE_CODE_VERSION, 60)
        assert (settings.github.step_outputs, settings.github.step_summary) == ("/dev/null", "/dev/null")
        assert not settings.github.in_actions

    @pytest.mark.parametrize(  # what action.yml passes for inputs nobody gave: empty variables
        "environ",
        [{name: "" for name in ("CE_PR", "CE_POST", "CE_FAIL_ON", "CE_SINCE", "CE_MODEL", "CE_MAX_TURNS")}],
        indirect=True,
    )
    def test_an_empty_variable_counts_as_unset(
        self,
        settings,
    ):
        assert (settings.review.pr, settings.review.post, settings.review.fail_on) == (None, True, "never")
        assert (settings.evaluation.since, settings.agent.model, settings.agent.max_turns) == (None, None, 60)

    @pytest.mark.parametrize(
        ("environ", "workspace"),
        [
            ({"GITHUB_WORKSPACE": "/runner/work/repo"}, "/runner/work/repo"),
            ({"CE_WORKSPACE": "/checkout", "GITHUB_WORKSPACE": "/runner/work/repo"}, "/checkout"),
        ],
        indirect=["environ"],
        ids=["the runner's", "the action's input first"],
    )
    def test_the_workspace_is_the_inputs_else_the_runners(
        self,
        settings,
        workspace,
    ):
        assert settings.run.workspace == workspace

    @pytest.mark.parametrize(
        ("environ", "out"),
        [
            ({"RUNNER_TEMP": "/runner/temp/"}, "/runner/temp/clean-eval"),
            ({"RUNNER_TEMP": "/runner/temp", "CE_OUTPUT_DIR": "/out"}, "/out"),
        ],
        indirect=["environ"],
        ids=["in the runner's temporary folder", "the action's input first"],
    )
    def test_the_output_folder_is_the_inputs_else_in_the_runners_temporary_one(
        self,
        settings,
        out,
    ):
        assert settings.run.out == out

    @pytest.mark.parametrize("environ", [{"ce_pr": "7", "Ce_Post": "false"}], indirect=True)
    def test_a_variable_is_read_by_its_exact_name(
        self,
        settings,
    ):
        assert (settings.review.pr, settings.review.post) == (None, True)

    def test_a_setting_names_the_variables_it_reads_in_order(
        self,
    ):
        assert Settings.variables("run.workspace") == ["CE_WORKSPACE", "GITHUB_WORKSPACE"]
        assert Settings.variables("review.pr") == ["CE_PR"]

    def test_the_settings_are_grouped_by_what_they_serve(
        self,
    ):
        groups = Settings.group_variables()
        assert list(groups) == [
            "run",
            "review",
            "evaluation",
            "heatmap",
            "agent",
            "github",
            "credentials",
            "federation",
        ]
        assert groups["review"] == {"CE_PR", "CE_POST", "CE_FAIL_ON"}
        assert groups["federation"] >= {"ANTHROPIC_FEDERATION_RULE_ID", "ACTIONS_ID_TOKEN_REQUEST_URL"}


class TestRefusals:
    """A value that doesn't fit its setting is refused before a run starts, named by its group and variable, and every
    such value at once."""

    @pytest.mark.parametrize(
        ("environ", "location"),
        [
            ({"CE_PR": "1; rm -rf /"}, "review.CE_PR"),
            ({"CE_FAIL_ON": "sometimes"}, "review.CE_FAIL_ON"),
            ({"CE_SINCE": "yesterday"}, "evaluation.CE_SINCE"),
            ({"CE_POST": "maybe"}, "review.CE_POST"),
            ({"CE_MAX_TURNS": "0"}, "agent.CE_MAX_TURNS"),
        ],
        indirect=["environ"],
        ids=[
            "a PR that is no number",
            "an unknown fail-on",
            "a date that is none",
            "a yes or no that is neither",
            "no turns",
        ],
    )
    def test_a_value_that_does_not_fit_is_refused(
        self,
        settings_environment,
        location,
    ):
        with pytest.raises(ValidationError) as refused:
            Settings()
        assert [".".join(str(part) for part in error["loc"]) for error in refused.value.errors()] == [location]

    @pytest.mark.parametrize(
        "environ", [{"CE_PR": "seven", "CE_SINCE": "yesterday", "CE_MAX_TURNS": "0"}], indirect=True
    )
    def test_every_value_that_does_not_fit_is_refused_together(
        self,
        settings_environment,
    ):
        with pytest.raises(ValidationError) as refused:
            Settings()
        assert [".".join(str(part) for part in error["loc"]) for error in refused.value.errors()] == [
            "review.CE_PR",
            "evaluation.CE_SINCE",
            "agent.CE_MAX_TURNS",
        ]


class TestNothingFromFiles:
    """The settings come from the environment only: in CI the working folder is the checkout under review."""

    def test_a_dotenv_file_in_the_working_folder_is_not_read(
        self,
        settings_environment,
        tmp_path,
        monkeypatch,
    ):
        Path(
            tmp_path,
            ".env",
        ).write_text("CE_FAIL_ON=blocker\nANTHROPIC_API_KEY=sk-ant-planted\n")
        monkeypatch.chdir(tmp_path)
        settings = Settings()
        assert (settings.review.fail_on, settings.credentials.api_key) == ("never", None)


class TestSecrets:
    """A credential is kept as a secret: no way of showing the settings shows it."""

    @pytest.mark.parametrize(
        "environ",
        [lfc(lambda key: {"ANTHROPIC_API_KEY": key, "GH_TOKEN": "ghs_" + "t" * 36}, lf("anthropic_api_key"))],
        indirect=True,
    )
    def test_a_credential_never_shows(
        self,
        settings,
        anthropic_api_key,
    ):
        for shown in (repr(settings), str(settings), settings.model_dump_json()):
            assert anthropic_api_key not in shown
            assert "ghs_" not in shown
        assert settings.credentials.api_key is not None
        assert settings.credentials.api_key.get_secret_value() == anthropic_api_key


class TestSignIns:
    """The settings report the ways to sign Claude in that are set, besides federation."""

    @pytest.mark.parametrize(
        ("environ", "sign_ins"),
        [
            ({}, []),
            ({"ANTHROPIC_API_KEY": "sk-ant-api03-key"}, ["ANTHROPIC_API_KEY"]),
            (
                {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-token", "CLAUDE_CODE_USE_BEDROCK": "1"},
                ["CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK"],
            ),
            ({"CLAUDE_CODE_USE_VERTEX": "0", "ANTHROPIC_FEDERATION_RULE_ID": "fdrl_example"}, []),
        ],
        indirect=["environ"],
        ids=["none", "a key", "a token and Bedrock", "federation, and Vertex off"],
    )
    def test_the_ways_to_sign_in_that_are_set(
        self,
        settings,
        sign_ins,
    ):
        assert settings.credentials.sign_ins == sign_ins


class TestTheActionsInputs:
    """action.yml passes each input to the command as a variable the settings read, and every CE_* setting is one of
    its inputs; the Claude Code it installs by default is the settings' default."""

    def test_the_action_and_the_settings_name_the_same_variables(
        self,
        action_yml,
    ):
        step = action_yml.split("    - id: run")[1]
        # what the run step sets but no setting reads: the interpreter, and the skill (the command's argument)
        passed = set(re.findall(r"^        ([A-Z_]+): ", step, re.MULTILINE)) - {"CE_PYTHON", "CE_SKILL"}
        read = {name for names in Settings.group_variables().values() for name in names}
        assert passed - read == set()
        assert {name for name in read if name.startswith("CE_")} - passed == set()

    def test_the_action_installs_the_claude_code_the_settings_default_to(
        self,
        action_yml,
    ):
        found = re.findall(r'claude-code-version:\n(?:    .*\n)*?    default: "([^"]+)"', action_yml)
        assert found == [CLAUDE_CODE_VERSION]
