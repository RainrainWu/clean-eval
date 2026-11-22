"""Fixtures for every layer's tests: the hermetic git config, throwaway repositories and fake `gh` / `claude`."""

import pytest

from tests.helpers import FakeBin, Repo

GITCONFIG = """[user]
\tname = Test
\temail = test@example.com
[commit]
\tgpgsign = false
[tag]
\tgpgsign = false
[init]
\tdefaultBranch = main
[protocol "file"]
\tallow = always
"""


@pytest.fixture(autouse=True, scope="session")
def hermetic_git(tmp_path_factory):
    """Every git call, in the tests and in the code under test, ignores the developer's git config."""
    config = tmp_path_factory.mktemp("git") / "gitconfig"
    config.write_text(GITCONFIG)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("GIT_CONFIG_GLOBAL", str(config))
        patch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        yield


@pytest.fixture
def repo(tmp_path):
    """A throwaway git repository (tests.helpers.Repo), on branch main."""
    return Repo(tmp_path / "repo")


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    """Fake `gh` and `claude` executables (tests.helpers.FakeBin), first on PATH."""
    fake = FakeBin(tmp_path / "bin")
    for name, value in fake.env().items():
        monkeypatch.setenv(name, value)
    return fake
