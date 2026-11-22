"""Check a release tag before the release workflow publishes it, and print what the workflow needs as step outputs.

usage: python -I .github/scripts/check_release.py <tag> [--branch origin/master]

A release tag is a semantic version: vMAJOR.MINOR.PATCH, or a pre-release vMAJOR.MINOR.PATCH-alpha.N, -beta.N or -rc.N
(the pre-releases Python versions can express too; no build metadata). It must:
  - point at a commit on the release branch (default: origin/master);
  - equal the version in pyproject.toml at that commit, as `uv version` writes it (1.2.0, or 1.3.0rc1 for v1.3.0-rc.1);
    `uv sync --locked` then checks uv.lock agrees;
  - be newer, by semver precedence, than every other release tag.

On success it prints `version=`, `sha=`, `major=` and `prerelease=` lines (for $GITHUB_OUTPUT); otherwise an ::error::
annotation on stderr, and it exits 1. Run it from the repository root: it reads only git and pyproject.toml."""

import argparse
import re
import subprocess
import sys
import tomllib

TAG = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?")  # the whole tag
PEP440_PRE = {"alpha": "a", "beta": "b", "rc": "rc"}


class ReleaseError(Exception):
    pass


def parse(tag):
    """(major, minor, patch, pre-release kind or None, pre-release number or None) of a release tag; None otherwise."""
    m = TAG.fullmatch(tag)
    if not m:
        return None
    major, minor, patch, kind, number = m.groups()
    return int(major), int(minor), int(patch), kind, None if number is None else int(number)


def precedence(version):
    """Sort key in semver precedence: a pre-release comes before its release, and alpha < beta < rc, then by number
    (Python's version ordering agrees)."""
    major, minor, patch, kind, number = version
    return (major, minor, patch, 1, "", 0) if kind is None else (major, minor, patch, 0, kind, number)


def python_version(version):
    """The version `uv version` writes to pyproject.toml for a release tag: 1.2.0, or 1.3.0rc1 for v1.3.0-rc.1."""
    major, minor, patch, kind, number = version
    return "%d.%d.%d" % (major, minor, patch) + ("" if kind is None else "%s%d" % (PEP440_PRE[kind], number))


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode:
        raise ReleaseError("git %s failed: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def check(tag, branch="origin/master"):
    """The step outputs for releasing `tag`; ReleaseError says why it can't be released."""
    version = parse(tag)
    if version is None:
        raise ReleaseError(
            "%s is not a release tag: use vMAJOR.MINOR.PATCH, or vMAJOR.MINOR.PATCH-alpha.N, -beta.N or "
            "-rc.N for a pre-release" % tag
        )
    sha = git("rev-parse", "--verify", tag + "^{commit}").strip()
    on_branch = subprocess.run(["git", "merge-base", "--is-ancestor", sha, branch], capture_output=True, text=True)
    if on_branch.returncode == 1:
        raise ReleaseError("%s (%s) is not on %s: releases are cut from %s only" % (tag, sha[:8], branch, branch))
    if on_branch.returncode:
        raise ReleaseError("cannot tell whether %s is on %s: %s" % (tag, branch, on_branch.stderr.strip()))
    try:
        declared = tomllib.loads(git("show", sha + ":pyproject.toml"))["project"]["version"]
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as e:
        raise ReleaseError("cannot read the project version in pyproject.toml at %s: %s" % (tag, e))
    if declared != python_version(version):
        raise ReleaseError(
            "%s needs version %s in pyproject.toml, which says %s at that commit: run `uv version %s` "
            "in a PR and tag its merge commit" % (tag, python_version(version), declared, tag[1:])
        )
    newer = sorted(
        (
            t
            for t in git("tag", "--list", "v*").split()
            if t != tag and parse(t) and precedence(parse(t)) >= precedence(version)
        ),
        key=lambda t: precedence(parse(t)),
    )
    if newer:
        raise ReleaseError("%s is not newer than the release tag(s) %s" % (tag, ", ".join(newer)))
    return {
        "version": tag[1:],
        "sha": sha,
        "major": "v%d" % version[0],
        "prerelease": str(version[3] is not None).lower(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check a release tag and print the release workflow's step outputs.")
    parser.add_argument("tag")
    parser.add_argument("--branch", default="origin/master", help="the branch releases are cut from")
    args = parser.parse_args(argv)
    try:
        outputs = check(args.tag, args.branch)
    except ReleaseError as e:
        print("::error::%s" % e, file=sys.stderr)
        return 1
    print(
        "%s (%s) is on %s, matches pyproject.toml and is newer than every release tag"
        % (args.tag, outputs["sha"][:8], args.branch),
        file=sys.stderr,
    )
    print("".join("%s=%s\n" % kv for kv in outputs.items()), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
