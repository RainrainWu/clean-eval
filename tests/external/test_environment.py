"""The environment and clock gateways (external/environment.py), on this process's environment: `process_env` sets the
variables of `environ` in it for the test. The secrets it names are checked by what redact() makes of text holding
them."""

import datetime as dt

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.external.environment import (
    ClockGateway,
    EnvironmentGateway,
)
from clean_eval.use_cases.rules.review import redact


@pytest.mark.parametrize("environ", [lf("variables_of_each_kind")], indirect=True)
class TestEnvironmentGateway:
    """The variables of this process's environment: path lists without their empty entries, and copies rather than the
    live environment."""

    def test_a_path_list_leaves_out_its_empty_entries(
        self,
        process_env,
    ):
        assert EnvironmentGateway().paths("CE_ROOTS") == ["/a", "/b c"]
        assert EnvironmentGateway().paths("CE_EMPTY") == []
        assert EnvironmentGateway().paths("CE_UNSET") == []

    def test_the_variables_are_a_copy_not_the_live_environment(
        self,
        process_env,
    ):
        snapshot = EnvironmentGateway().variables()
        snapshot["CE_X"] = "changed"
        assert process_env["CE_X"] == "1"


class TestSecrets:
    """What a secret looks like: the value of a variable named like one (8 characters or more) and its base64 forms, and
    the well-known credential formats, among them an authorization header's credential (the header's name is kept)."""

    @pytest.mark.parametrize(
        "environ",
        [{"MY_API_TOKEN": "s3cr3t-value-123", "HARMLESS": "s3cr3t-value-123x"}],
        indirect=True,
    )
    def test_secret_values_of_the_environment_and_token_formats(
        self,
        process_env,
        github_token,
        anthropic_api_key,
    ):
        text, count = redact(
            "leak s3cr3t-value-123 and %s and %s" % (github_token, anthropic_api_key),
            EnvironmentGateway().secrets(),
        )
        assert "s3cr3t" not in text
        assert "ghp_" not in text
        assert "sk-ant-" not in text
        assert count == 3

    @pytest.mark.parametrize("environ", [{"GH_TOKEN": lf("github_app_token")}], indirect=True)
    def test_authorization_headers_and_base64_forms(
        self,
        process_env,
        github_app_token_basic,
    ):
        text, count = redact(
            "extraheader AUTHORIZATION: basic %s\nraw %s" % (github_app_token_basic, github_app_token_basic),
            EnvironmentGateway().secrets(),
        )
        assert github_app_token_basic not in text
        assert "AUTHORIZATION: basic [REDACTED]" in text
        assert count >= 2

    @pytest.mark.parametrize("environ", [{"SHORT_TOKEN": "abc", "PATH_HINT": "/usr/bin/local"}], indirect=True)
    def test_short_values_and_variables_not_named_as_secrets_are_kept(
        self,
        process_env,
    ):
        assert redact("abc /usr/bin/local", EnvironmentGateway().secrets())[1] == 0

    def test_an_authorization_header_keeps_its_name(
        self,
    ):
        text, count = redact("Authorization: Bearer abcdefgh12345678", EnvironmentGateway().secrets())
        assert (text, count) == ("Authorization: Bearer [REDACTED]", 1)


class TestClockGateway:
    """Today's date, the only time clean-eval reads the clock."""

    def test_today_is_the_local_date(
        self,
    ):
        assert ClockGateway().today() == dt.date.today().isoformat()
