"""The environment and clock gateways (external/environment.py), on this process's environment: `process_env` sets the
variables of `environ` in it for the test."""

import datetime as dt

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.external.environment import (
    ClockGateway,
    EnvironmentGateway,
)


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


class TestClockGateway:
    """Today's date, the only time clean-eval reads the clock."""

    def test_today_is_the_local_date(
        self,
    ):
        assert ClockGateway().today() == dt.date.today().isoformat()
