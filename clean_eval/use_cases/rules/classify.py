"""Rules that classify paths and names: tests, test scales, manifests, tooling, core-flow names, ignored paths."""

import fnmatch
import functools
import re


@functools.lru_cache(maxsize=128)
def _rx(pattern):
    return re.compile(pattern)


def is_test(path, policy):
    parts = path.split("/")
    return any(x in policy.test_dirs for x in parts[:-1]) or bool(_rx(policy.test_file).match(parts[-1]))


def classify_scale(path):
    p = path.lower()
    if "e2e" in p or "end_to_end" in p:
        return "e2e"
    if "integration" in p or "live" in p or "smoke" in p:
        return "integration"
    if "unit" in p or "/tests/" in "/" + p or "/test/" in "/" + p:
        return "unit"
    return "other"


def not_applicable(module_name, policy):
    """Reason a module is not rated for Unverified (manifest or test code), else None."""
    for reason, pattern in policy.not_applicable:
        if _rx(pattern).search(module_name.lower()):
            return reason
    return None


def has_core_keyword(module_name, policy):
    return bool(_rx(policy.keywords).search(module_name.lower()))


def looks_like_tooling(module_name, first_path, policy):
    rx = _rx(policy.tooling)
    return bool(rx.search(module_name.lower()) or rx.search(first_path.lower()))


def is_migration(path, policy):
    return bool(_rx(policy.migration_path).search(path))


def excluded(path, policy):
    """Source the policy leaves out of the ratings (vendored or generated code)."""
    return any(fnmatch.fnmatch(path, g) for g in policy.exclude)


def ignored(path, policy):
    """A changed path that carries no risk of its own (documentation, images)."""
    return any(fnmatch.fnmatch(path, g) for g in policy.ignore_paths)
