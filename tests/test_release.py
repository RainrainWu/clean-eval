"""The release check (.github/scripts/check_release.py) the release workflow runs on a pushed tag: which tags may be
released, and the step outputs the workflow publishes from. Run against throwaway repositories, as the workflow runs it
(the `released` and `release_check` fixtures)."""

import pytest

RELEASE_TAGS = ["v1.0.0", "v0.1.0", "v10.20.30", "v1.2.3-alpha.0", "v1.2.3-beta.2", "v1.2.3-rc.11"]
NOT_RELEASE_TAGS = [
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
]
SEMVER_ORDER = [  # lowest first
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
# Steps after release 1.0.0 (see the `released` fixture).
RC_ANNOTATED = [{"version": "1.1.0rc1", "tag": "v1.1.0-rc.1", "annotated": True}]
BUMP_FORGOTTEN = [{"message": "forgot the bump", "files": {"README.md": "x\n"}, "tag": "v1.1.0"}]
OFF_THE_BRANCH = [{"branch": "side", "version": "1.1.0"}]
OLDER_AFTER_NEWER = [{"version": "1.2.0"}, {"version": "1.0.1"}, {"tag": "v1", "at": "v1.2.0"}]  # v1: the major tag
NOT_SEMVER = [{"tag": "v1.0", "at": "v1.0.0"}]


class TestReleaseTags:
    """Which tags are release tags, how they sort, and the version pyproject.toml must hold for one."""

    @pytest.mark.parametrize("tag", RELEASE_TAGS)
    def test_a_release_tag_is_semver_that_a_python_version_can_express(
        self,
        check_release,
        tag,
    ):
        assert check_release.parse(tag) is not None

    @pytest.mark.parametrize("tag", NOT_RELEASE_TAGS, ids=repr)
    def test_anything_else_is_not_a_release_tag(
        self,
        check_release,
        tag,
    ):
        assert check_release.parse(tag) is None

    def test_precedence_follows_semver(
        self,
        check_release,
    ):
        ordered = sorted(reversed(SEMVER_ORDER), key=lambda tag: check_release.precedence(check_release.parse(tag)))
        assert ordered == SEMVER_ORDER

    def test_the_pyproject_version_is_what_uv_writes(
        self,
        check_release,
    ):
        assert [
            check_release.python_version(check_release.parse(tag))
            for tag in ("v1.2.0", "v1.3.0-rc.1", "v2.0.0-alpha.0", "v2.0.0-beta.3")
        ] == ["1.2.0", "1.3.0rc1", "2.0.0a0", "2.0.0b3"]


class TestCheck:
    """The check of a tag pushed to a repository whose first commit is release 1.0.0, tagged v1.0.0."""

    def test_a_release_gives_the_workflow_its_outputs(
        self,
        released,
        release_check,
    ):
        first = released.git("rev-parse", "HEAD")
        expected = "version=1.0.0\nsha=%s\nmajor=v1\nprerelease=false\n" % first
        assert (release_check.returncode, release_check.stdout) == (0, expected), release_check.stderr

    @pytest.mark.parametrize("released", [RC_ANNOTATED], indirect=True)
    @pytest.mark.parametrize("release_check", ["v1.1.0-rc.1"], indirect=True)
    def test_an_annotated_pre_release_tag(
        self,
        released,
        release_check,
    ):
        sha = released.git("rev-parse", "HEAD")
        expected = "version=1.1.0-rc.1\nsha=%s\nmajor=v1\nprerelease=true\n" % sha
        assert (release_check.returncode, release_check.stdout) == (0, expected), release_check.stderr

    @pytest.mark.parametrize("released", [BUMP_FORGOTTEN], indirect=True)
    @pytest.mark.parametrize("release_check", ["v1.1.0"], indirect=True)
    def test_the_version_must_be_the_one_in_pyproject(
        self,
        release_check,
    ):
        assert (release_check.returncode, release_check.stdout) == (1, "")
        assert "::error::v1.1.0 needs version 1.1.0 in pyproject.toml, which says 1.0.0" in release_check.stderr

    @pytest.mark.parametrize("released", [OFF_THE_BRANCH], indirect=True)
    @pytest.mark.parametrize("release_check", ["v1.1.0"], indirect=True)
    def test_the_tag_must_be_on_the_release_branch(
        self,
        release_check,
    ):
        assert release_check.returncode == 1
        assert "is not on main" in release_check.stderr

    @pytest.mark.parametrize("released", [OLDER_AFTER_NEWER], indirect=True)
    @pytest.mark.parametrize(
        ("release_check", "status", "error"),
        [
            ("v1.0.1", 1, "::error::v1.0.1 is not newer than the release tag(s) v1.2.0"),
            ("v1.2.0", 0, ""),
        ],
        indirect=["release_check"],
        ids=["older", "newest"],
    )
    def test_the_tag_must_be_newer_than_every_release(
        self,
        release_check,
        status,
        error,
    ):
        assert release_check.returncode == status, release_check.stderr
        assert error in release_check.stderr

    @pytest.mark.parametrize("released", [NOT_SEMVER], indirect=True)
    @pytest.mark.parametrize("release_check", ["v1.0"], indirect=True)
    def test_a_tag_that_is_not_a_release_tag(
        self,
        release_check,
    ):
        assert release_check.returncode == 1
        assert "v1.0 is not a release tag" in release_check.stderr
