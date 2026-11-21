"""SourceReaders over git: the Python files of a working tree, or of any revision read straight from git objects.
Files are only read as text; nothing from the repository is imported or run."""
import hashlib
import os
import re

from ...domain.classify import is_test
from ...domain.entities import SourceFile, Snapshot

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


class WorkingTreeSource:
    """The tracked Python files of the working tree, as they are on disk now."""

    def __init__(self, git, policy):
        self.git, self.policy = git, policy
        self.root = git.toplevel()

    def snapshot(self):
        sha = self.git.rev_parse("HEAD", short=True)
        return Snapshot(repo=self.git.repo_name(), ref=self.git.branch() or sha, sha=sha, root=self.root)

    def read(self):
        out = []
        for rel in self.git.ls_files():
            if not _wanted(rel):
                continue
            path = os.path.join(self.root, rel)
            try:
                if os.path.islink(path) or os.path.getsize(path) > MAX_FILE_BYTES:
                    continue  # a symlink could point outside the repository
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            out.append(SourceFile(rel, text, count_loc(text), is_test(rel, self.policy), content_id(text)))
        return out


class RevisionSource:
    """The Python files of a revision, read from git objects (no checkout). `blob_cache` is shared across revisions."""

    def __init__(self, git, rev, policy, blob_cache=None):
        self.git, self.rev, self.policy = git, rev, policy
        self.cache = {} if blob_cache is None else blob_cache
        self.root = git.toplevel()

    def snapshot(self):
        return Snapshot(repo=self.git.repo_name(), ref=self.rev, sha=self.git.rev_parse(self.rev, short=True), root=self.root)

    def read(self):
        entries = [(p, b) for p, b, size in self.git.ls_tree(self.rev) if _wanted(p) and size <= MAX_FILE_BYTES]
        missing = [b for _, b in entries if b not in self.cache]
        self.cache.update(self.git.read_blobs(missing))
        return [SourceFile(p, self.cache[b], count_loc(self.cache[b]), is_test(p, self.policy), b)
                for p, b in entries if b in self.cache]
