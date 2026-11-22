"""Unified diffs as text: one patch per file, size limits, and the lines GitHub accepts inline comments on."""

import re

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def split_patch(full):
    """Split a `git diff` into one chunk per file (each starts with 'diff --git ')."""
    chunks, cur = [], []
    for line in full.splitlines(keepends=True):
        if line.startswith("diff --git ") and cur:
            chunks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def truncate(text, limit):
    """(text cut to `limit` characters with a note saying how much is missing, whether it was cut)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[... truncated: %d more characters ...]\n" % (len(text) - limit), True


def line_counts(patch):
    """(lines added, lines removed) by a patch."""
    lines = patch.splitlines()
    return (
        sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
        sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
    )


def commentable_lines(patch):
    """The new-side line numbers of one file's patch that GitHub accepts inline comments on: added and context lines."""
    lines, new = set(), None
    for line in patch.splitlines():
        m = HUNK.match(line)
        if m:
            new = int(m.group(1))
        elif new is None or line.startswith(("-", "\\")):
            continue  # file header, removed line, or "\ No newline at end of file"
        else:  # added ("+") or context (" ") line on the new side
            lines.add(new)
            new += 1
    return lines
