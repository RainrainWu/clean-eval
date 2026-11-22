"""Diffs (use_cases/rules/diff.py): a combined patch split into one per file."""

from clean_eval.use_cases.rules.diff import split_patch


def test_split_patch():
    full = "diff --git a/x b/x\n+1\ndiff --git a/y b/y\n-2\n"
    assert split_patch(full) == ["diff --git a/x b/x\n+1\n", "diff --git a/y b/y\n-2\n"]
    assert split_patch("") == []
