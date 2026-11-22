"""The sources to rate, through the Git and FileSystem interfaces: the Python files of a working tree, or of any revision
read straight from git objects. Files are only read as text; nothing from the repository is imported or run."""

import hashlib
import re

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.source import Snapshot, SourceFile
from clean_eval.use_cases.rules.classify import is_test

GENERATED = re.compile(r"(_pb2(_grpc)?\.py$|/generated/|/vendor/|/site-packages/)")
MAX_FILE_BYTES = 1_500_000


def count_loc(text):
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _wanted(path):
    return path.endswith(".py") and not GENERATED.search("/" + path)


def content_id(text):
    """Same id as `git hash-object` for UTF-8 text, so working-tree and revision reads share caches."""
    data = text.encode("utf-8", "surrogateescape")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def working_tree_snapshot(git):
    sha = git.rev_parse("HEAD", short=True)
    return Snapshot(repo=git.repo_name(), ref=git.branch() or sha, sha=sha, root=git.toplevel())


def revision_snapshot(git, rev):
    return Snapshot(repo=git.repo_name(), ref=rev, sha=git.rev_parse(rev, short=True), root=git.toplevel())


def read_working_tree(git, fs, policy):
    """(Snapshot, the tracked Python files of the working tree as they are on disk now)."""
    snapshot, out = working_tree_snapshot(git), []
    for rel in git.ls_files():
        if not _wanted(rel):
            continue
        path = fs.join(snapshot.root, rel)
        # a symlink could point outside the repository; a tracked file may be gone from the working tree
        if fs.is_link(path) or not fs.is_file(path) or fs.size(path) > MAX_FILE_BYTES:
            continue
        try:
            text = fs.read_text(path)
        except HeatmapError:
            continue
        out.append(
            SourceFile(path=rel, text=text, loc=count_loc(text), is_test=is_test(rel, policy), blob=content_id(text))
        )
    return snapshot, out


def read_revision(git, rev, policy, blobs=None):
    """(Snapshot, the Python files of a revision), read from git objects (no checkout). `blobs` ({blob id: text}, e.g. a
    RatingCache's) is shared across revisions, so each file version is read once."""
    blobs = {} if blobs is None else blobs
    entries = [(p, b) for p, b, size in git.ls_tree(rev) if _wanted(p) and size <= MAX_FILE_BYTES]
    blobs.update(git.read_blobs([b for _, b in entries if b not in blobs]))
    files = [
        SourceFile(path=p, text=blobs[b], loc=count_loc(blobs[b]), is_test=is_test(p, policy), blob=b)
        for p, b in entries
        if b in blobs
    ]
    return revision_snapshot(git, rev), files
