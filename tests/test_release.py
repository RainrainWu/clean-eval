"""The release check (.github/scripts/check_release.py) the release workflow runs on a pushed tag: which tags may be
released, and the step outputs the workflow publishes from. Run against throwaway repositories, as the workflow runs it."""

import importlib.util
import os
import subprocess
import sys

import pytest

from tests.helpers import ROOT, days_ago

SCRIPT = os.path.join(ROOT, ".github", "scripts", "check_release.py")
_spec = importlib.util.spec_from_file_location("check_release", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
check_release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_release)


def pyproject(version):
    return '[project]\nname = "x"\nversion = "%s"\n' % version


def key(tag):
    return check_release.precedence(check_release.parse(tag))


class TestReleaseTags:
    @pytest.mark.parametrize(
        "tag", ["v1.0.0", "v0.1.0", "v10.20.30", "v1.2.3-alpha.0", "v1.2.3-beta.2", "v1.2.3-rc.11"]
    )
    def test_a_release_tag_is_semver_that_a_python_version_can_express(self, tag):
        assert check_release.parse(tag) is not None

    @pytest.mark.parametrize(
        "tag",
        [
            "1.0.0",
            "v1",
            "v1.0",
            "v01.0.0",
            "v1.0.0-rc",
            "v1.0.0-rc.01",
            "v1.0.0-RC.1",
            "v1.0.0-foo.1",
            "v1.0.0+build.1",
            "v1.0.0\n",
        ],
        ids=repr,
    )
    def test_anything_else_is_not_a_release_tag(self, tag):
        assert check_release.parse(tag) is None

    def test_precedence_follows_semver(self):
        order = [
            "v1.0.0-alpha.1",
            "v1.0.0-alpha.2",
            "v1.0.0-beta.2",
            "v1.0.0-beta.10",
            "v1.0.0-rc.1",
            "v1.0.0",
            "v1.0.1",
            "v1.1.0-rc.1",
            "v1.1.0",
            "v1.10.0",
            "v2.0.0",
        ]
        assert sorted(reversed(order), key=key) == order

    def test_the_pyproject_version_is_what_uv_writes(self):
        assert [
            check_release.python_version(check_release.parse(t))
            for t in ("v1.2.0", "v1.3.0-rc.1", "v2.0.0-alpha.0", "v2.0.0-beta.3")
        ] == ["1.2.0", "1.3.0rc1", "2.0.0a0", "2.0.0b3"]


class TestCheck:
    @pytest.fixture(autouse=True)
    def released(self, repo):
        """A repository whose first commit is release 1.0.0, tagged v1.0.0."""
        self.repo = repo
        self.first = self.repo.commit("release 1.0.0", {"pyproject.toml": pyproject("1.0.0")}, when=days_ago(5))
        self.repo.git("tag", "v1.0.0", self.first)

    def check(self, tag):
        """(exit status, stdout, stderr) of the check on the repository, run as the release workflow runs it."""
        r = subprocess.run(
            [sys.executable, "-I", SCRIPT, tag, "--branch", "main"], cwd=self.repo.path, capture_output=True, text=True
        )
        return r.returncode, r.stdout, r.stderr

    def release(self, version, tag=None, annotated=False):
        sha = self.repo.commit("release " + version, {"pyproject.toml": pyproject(version)}, when=days_ago(4))
        self.repo.git("tag", *(["-a", "-m", "release"] if annotated else []), tag or "v" + version, sha)
        return sha

    def test_a_release_gives_the_workflow_its_outputs(self):
        code, out, err = self.check("v1.0.0")
        assert (code, out) == (0, "version=1.0.0\nsha=%s\nmajor=v1\nprerelease=false\n" % self.first), err

    def test_an_annotated_pre_release_tag(self):
        sha = self.release("1.1.0rc1", "v1.1.0-rc.1", annotated=True)
        code, out, err = self.check("v1.1.0-rc.1")
        assert (code, out) == (0, "version=1.1.0-rc.1\nsha=%s\nmajor=v1\nprerelease=true\n" % sha), err

    def test_the_version_must_be_the_one_in_pyproject(self):
        sha = self.repo.commit("forgot the bump", {"README.md": "x\n"}, when=days_ago(4))
        self.repo.git("tag", "v1.1.0", sha)
        code, out, err = self.check("v1.1.0")
        assert (code, out) == (1, "")
        assert "::error::v1.1.0 needs version 1.1.0 in pyproject.toml, which says 1.0.0" in err

    def test_the_tag_must_be_on_the_release_branch(self):
        self.repo.git("checkout", "-q", "-b", "side")
        self.release("1.1.0")
        code, _, err = self.check("v1.1.0")
        assert code == 1
        assert "is not on main" in err

    def test_the_tag_must_be_newer_than_every_release(self):
        self.release("1.2.0")
        self.release("1.0.1")
        self.repo.git("tag", "v1", "v1.2.0")  # the major tag is not a release tag
        code, _, err = self.check("v1.0.1")
        assert code == 1
        assert "::error::v1.0.1 is not newer than the release tag(s) v1.2.0" in err
        assert self.check("v1.2.0")[0] == 0

    def test_a_tag_that_is_not_a_release_tag(self):
        self.repo.git("tag", "v1.0", self.first)
        code, _, err = self.check("v1.0")
        assert code == 1
        assert "v1.0 is not a release tag" in err
