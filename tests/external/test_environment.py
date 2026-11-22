"""The environment gateway (external/environment.py): variables, path lists, and copies rather than the live
environment."""

import os

from clean_eval.external.environment import EnvironmentGateway


def test_environment(monkeypatch):
    monkeypatch.setenv("CE_ROOTS", os.pathsep.join(["/a", "", "/b c"]))
    monkeypatch.setenv("CE_X", "1")
    monkeypatch.delenv("CE_UNSET", raising=False)
    env = EnvironmentGateway()
    assert env.paths("CE_ROOTS") == ["/a", "/b c"]
    assert env.paths("CE_UNSET") == []
    assert (env.get("CE_X"), env.get("CE_UNSET", "d")) == ("1", "d")
    snapshot = env.variables()
    snapshot["CE_X"] = "changed"
    assert os.environ["CE_X"] == "1"  # a copy, not the live environment
