"""The git CLI: every git command clean-eval runs, as the use cases' Git interface: the sources of a working tree or of
any revision, the history, diffs and patches. Everything about git's formats stays here too: a unified diff read into
FileDiffs, and the ids git gives files' contents. Paths are passed raw (NUL-delimited output, literal pathspecs), so any
filename survives; nothing in the repository is executed."""

import hashlib
import re
import subprocess
from typing import Optional

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.review import FileDiff
from clean_eval.use_cases.interfaces import Git

# A hunk's header in a unified diff: where the hunk starts in the new file, and how many of its lines it shows (1 when
# left out).
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def run_git(
    root: str,
    *args: str,
    check: bool = True,
) -> str:
    """The output of `git <args>` in the repository at `root`; HeatmapError when it fails and `check` is on."""
    completed = subprocess.run(
        ["git", "-c", "core.quotePath=false", "-C", root, *args],
        capture_output=True,
        text=True,
        errors="surrogateescape",
    )
    if check and completed.returncode != 0:
        raise HeatmapError(
            "git %s failed: %s" % (" ".join(args[:3]), completed.stderr.strip()[-300:]),
        )
    return completed.stdout


def split_patch(
    full: str,
) -> list[str]:
    """Split a `git diff` into one patch per file (each starts with 'diff --git ')."""
    chunks, cur = [], []
    for line in full.splitlines(keepends=True):
        if line.startswith("diff --git ") and cur:
            chunks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def line_counts(
    patch: str,
) -> tuple[int, int]:
    """(lines added, lines removed) by a patch."""
    lines = patch.splitlines()
    return (
        sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
        sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
    )


def hunk_lines(
    patch: str,
) -> list[tuple[int, int]]:
    """The lines of the new file each hunk of a file's patch shows, as (first, last): its added and unchanged lines. A
    hunk that shows none (the file removed) has none."""
    ranges = []
    for line in patch.splitlines():
        hunk = HUNK.match(line)
        if hunk:
            first, shown = int(hunk.group(1)), int(hunk.group(2) or "1")
            if shown:
                ranges.append((first, first + shown - 1))
    return ranges


def file_diff(
    path: str,
    patch: str,
) -> FileDiff:
    """The FileDiff of `path`, from its patch."""
    added, removed = line_counts(patch)
    return FileDiff(
        path=path,
        patch=patch,
        added=added,
        removed=removed,
        hunks=hunk_lines(patch),
    )


class GitGateway(Git):
    def __init__(
        self,
        root: str,
    ) -> None:
        self.root = root

    def toplevel(
        self,
    ) -> str:
        return run_git(self.root, "rev-parse", "--show-toplevel").strip()

    def is_shallow(
        self,
    ) -> bool:
        return run_git(self.root, "rev-parse", "--is-shallow-repository").strip() == "true"

    def rev_parse(
        self,
        rev: str,
        short: bool = False,
    ) -> str:
        return run_git(self.root, "rev-parse", *(["--short"] if short else []), "--verify", rev + "^{commit}").strip()

    def has_commit(
        self,
        rev: str,
    ) -> bool:
        return (
            subprocess.run(
                ["git", "-C", self.root, "cat-file", "-e", rev + "^{commit}"], capture_output=True
            ).returncode
            == 0
        )

    def merge_base(
        self,
        first_rev: str,
        second_rev: str,
    ) -> str:
        return run_git(self.root, "merge-base", first_rev, second_rev).strip()

    def branch(
        self,
    ) -> str:
        name = run_git(self.root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        return name if name and name != "HEAD" else ""

    def repo_name(
        self,
    ) -> str:
        url = run_git(self.root, "config", "--get", "remote.origin.url", check=False).strip()
        name = re.sub(r"\.git$", "", url.rstrip("/")).split("/")[-1].split(":")[-1] if url else ""
        return name or self.toplevel().rstrip("/").rsplit("/", 1)[-1]

    def ls_files(
        self,
    ) -> list[str]:
        return [p for p in run_git(self.root, "ls-files", "-z").split("\0") if p]

    def ls_tree(
        self,
        rev: str,
    ) -> list[tuple[str, str, int]]:
        """[(path, blob id, size)] of the files at `rev`."""
        out = []
        for entry in run_git(self.root, "ls-tree", "-r", "-z", "--long", rev).split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            mode, kind, blob, size = meta.split()
            if kind == "blob" and size.isdigit():
                out.append((path, blob, int(size)))
        return out

    def read_blobs(
        self,
        blob_ids: list[str],
    ) -> dict[str, str]:
        """{blob id: text} for the given blobs, read through one `git cat-file --batch` process."""
        ids = list(dict.fromkeys(blob_ids))
        if not ids:
            return {}
        p = subprocess.run(
            ["git", "-C", self.root, "cat-file", "--batch"], input="\n".join(ids).encode() + b"\n", capture_output=True
        )
        if p.returncode != 0:
            raise HeatmapError(
                "git cat-file failed: %s" % p.stderr.decode(errors="replace")[-300:],
            )
        out, data, pos = {}, p.stdout, 0
        for _ in ids:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].decode().split()
            pos = nl + 1
            if len(header) < 3 or header[1] == "missing":
                continue
            size = int(header[2])
            out[header[0]] = data[pos : pos + size].decode("utf-8", "ignore")
            pos += size + 1
        return out

    def diff_files(
        self,
        base: str,
        head: str,
    ) -> list[str]:
        """Paths changed between two revisions (raw, NUL-delimited)."""
        return [p for p in run_git(self.root, "diff", "--name-only", "-z", base, head).split("\0") if p]

    def show(
        self,
        rev: str,
        path: str,
    ) -> Optional[str]:
        """Text of the file `path` at `rev`, or None when `rev` has no such file. Any other git failure (a bad revision,
        a broken repository) raises, so a caller never mistakes an error for "not configured"."""
        listing = run_git(self.root, "--literal-pathspecs", "ls-tree", "-r", "-z", rev, "--", path)
        blob = None
        for entry in listing.split("\0"):
            meta, _, name = entry.partition("\t")
            if name == path and meta.split()[1:2] == ["blob"]:
                blob = meta.split()[2]
        if blob is None:
            return None
        r = subprocess.run(["git", "-C", self.root, "cat-file", "blob", blob], capture_output=True)
        if r.returncode != 0:
            raise HeatmapError(
                "git cat-file %s (%s at %s) failed: %s" % (blob, path, rev, r.stderr.decode(errors="replace")[-300:])
            )
        return r.stdout.decode("utf-8", "replace")

    def ensure_commit(
        self,
        sha: str,
        refspec: str,
    ) -> None:
        """Fetch `refspec` from origin when `sha` is not here yet."""
        if not self.has_commit(sha):
            run_git(self.root, "fetch", "--quiet", "origin", refspec, check=False)
        if not self.has_commit(sha):
            raise HeatmapError(
                "commit %s is not available locally (checkout with fetch-depth: 0)" % sha,
            )

    def file_diffs(
        self,
        base: str,
        head: str,
        paths: list[str],
    ) -> list[FileDiff]:
        """One diff of every path, renames detected, split per file; when that doesn't give one patch per path (git
        shows a rename as one section for two paths), the diff of each path (taken literally) on its own."""
        patches = split_patch(run_git(self.root, "diff", "-M", base, head))
        if len(patches) != len(paths):
            patches = [run_git(self.root, "--literal-pathspecs", "diff", base, head, "--", path) for path in paths]
        return [file_diff(path, patch) for path, patch in zip(paths, patches)]

    def blob_id(
        self,
        text: str,
    ) -> str:
        data = text.encode("utf-8", "surrogateescape")
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def file_log(
        self,
        rev: str,
        path: str,
        limit: int = 10,
    ) -> str:
        return run_git(
            self.root,
            "--literal-pathspecs",
            "log",
            "--format=%h %ad %an: %s",
            "--date=short",
            "-n",
            str(limit),
            rev,
            "--",
            path,
        ).strip()

    def commit_patch(
        self,
        sha: str,
        paths: list[str],
    ) -> str:
        return run_git(
            self.root,
            "--literal-pathspecs",
            "show",
            "--stat",
            "-p",
            "--format=%H%n%an %ad%n%s%n%n%b",
            sha,
            "--",
            *paths,
        )

    def default_branch(
        self,
    ) -> str:
        """origin/HEAD, else origin/main|master (CI checkouts don't set origin/HEAD), else HEAD."""
        ref = run_git(self.root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD", check=False).strip()
        if ref:
            return ref
        for ref in ("origin/main", "origin/master", "main", "master"):
            if self.has_commit(ref):
                return ref
        return "HEAD"

    def first_parent_log(
        self,
        branch: str,
        since: str,
        until: str,
    ) -> list[tuple[str, list[str], str, int, str]]:
        """[(sha, [parent shas], author, unix time, subject)] of the first-parent commits of `branch` in the period."""
        out = []
        for line in run_git(
            self.root,
            "log",
            "--first-parent",
            branch,
            "--since=%s" % since,
            "--until=%s" % until,
            "--format=%H%x1f%P%x1f%an%x1f%ct%x1f%s",
        ).splitlines():
            sha, parents, author, ts, subject = line.split("\x1f", 4)
            out.append((sha, parents.split(), author, int(ts), subject))
        return out
