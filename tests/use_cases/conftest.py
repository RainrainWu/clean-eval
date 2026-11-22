"""Fixtures for the use cases' tests: empty in-memory fakes of the Git and FileSystem interfaces. A module that needs
contents overrides them with a fixture of the same name that asks for (and fills) these."""

import pytest

from tests.use_cases.fakes import MemoryFileSystem, MemoryGit


@pytest.fixture
def fs():
    """An empty MemoryFileSystem."""
    return MemoryFileSystem()


@pytest.fixture
def git():
    """A MemoryGit with no commits yet: repository "demo" at /repo, on branch main."""
    return MemoryGit()
