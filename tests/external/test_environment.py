"""The environment and clock gateways (external/environment.py), on this process's environment: `process_env` sets the
variables of `environ` in it for the test."""

import datetime as dt
import os

import pytest

from clean_eval.external.environment import ClockGateway, EnvironmentGateway

# A path list with an empty entry, an empty one (how the guard gets no roots), a plain variable, and one that
# is not set.
VARIABLES = {"CE_ROOTS": os.pathsep.join(["/a", "", "/b c"]), "CE_EMPTY": "", "CE_X": "1", "CE_UNSET": None}


@pytest.mark.parametrize("environ", [VARIABLES], indirect=True)
class TestEnvironmentGateway:
    """The variables of this process's environment: path lists without their empty entries, defaults for unset
    variables, and copies rather than the live environment."""

    def test_a_path_list_leaves_out_its_empty_entries(
        self,
        process_env,
    ):
        assert EnvironmentGateway().paths("CE_ROOTS") == ["/a", "/b c"]
        assert EnvironmentGateway().paths("CE_EMPTY") == []
        assert EnvironmentGateway().paths("CE_UNSET") == []

    def test_a_variable_or_the_default_when_it_is_unset(
        self,
        process_env,
    ):
        assert (EnvironmentGateway().get("CE_X"), EnvironmentGateway().get("CE_UNSET", "d")) == ("1", "d")

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
