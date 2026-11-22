"""Fakes of the use cases' interfaces (clean_eval/use_cases/interfaces.py), so the use-case tests run in memory: a
one-line fake of any interface, a file system and a git repository. MemoryFileSystem and MemoryGit behave as the
interfaces document; tests/external/test_contracts.py runs them and the real gateways through the same checks."""

import datetime as dt
import difflib
import fnmatch
import hashlib
import posixpath

from clean_eval.domain.errors import HeatmapError
from clean_eval.use_cases.interfaces import FileSystem, Git


def fake(interface, **members):
    """An implementation of one of the interfaces in a line: `members` are its methods (as plain functions, without
    `self`) and attributes. Calling any other abstract method fails the test, so a fake also shows which methods a use
    case needs."""

    def unexpected(name):
        def method(self, *args, **kwargs):
            raise AssertionError("unexpected call: %s.%s" % (interface.__name__, name))

        return method

    body = {name: unexpected(name) for name in interface.__abstractmethods__}
    body.update({name: staticmethod(m) if callable(m) else m for name, m in members.items()})
    return type("Fake" + interface.__name__, (interface,), body)()


class MemoryFileSystem(FileSystem):
    """Files, folders and symlinks in memory, at POSIX paths (relative ones resolve against `cwd`)."""

    sep = "/"

    def __init__(self, files=None, cwd="/work"):
        self.files, self.links, self.dirs, self._cwd = {}, {}, {"/"}, cwd
        for path, text in (files or {}).items():
            self.write_text(path, text)

    def link(self, path, target):
        """Make `path` a symlink to `target`."""
        self.make_dirs(posixpath.dirname(self._norm(path)))
        self.links[self._norm(path)] = self._norm(target)

    def _norm(self, path):
        return posixpath.normpath(posixpath.join(self._cwd, path))

    def _resolve(self, path):
        current = "/"
        for part in self._norm(path).strip("/").split("/"):
            current, hops = posixpath.join(current, part), 0
            while current in self.links:
                current, hops = self.links[current], hops + 1
                if hops > 40:
                    raise HeatmapError("too many levels of symlinks in %s" % path)
        return current

    def _under(self, paths, top):
        return [p for p in paths if p.startswith(top.rstrip("/") + "/")]

    # paths
    def join(self, *parts):
        return posixpath.join(*parts)

    def parent(self, path):
        return posixpath.dirname(path)

    def name(self, path):
        return posixpath.basename(path)

    def relative(self, path, start):
        return posixpath.relpath(self._norm(path), self._norm(start))

    def absolute(self, path):
        return self._norm(path)

    def real_path(self, path):
        return self._resolve(path)

    def expand_user(self, path):
        return "/home/user" + path[1:] if path == "~" or path.startswith("~/") else path

    def cwd(self):
        return self._cwd

    # what is there
    def exists(self, path):
        resolved = self._resolve(path)
        return resolved in self.files or resolved in self.dirs

    def lexists(self, path):
        return self._norm(path) in self.links or self.exists(path)

    def is_file(self, path):
        return self._resolve(path) in self.files

    def is_dir(self, path):
        return self._resolve(path) in self.dirs

    def is_link(self, path):
        return self._norm(path) in self.links

    def size(self, path):
        return len(self.read_text(path).encode())

    def files_under(self, top):
        top = self._norm(top)
        if top not in self.dirs:
            return []
        listed = self._under(self.files, top) + [
            p for p in self._under(self.links, top) if self.links[p] not in self.dirs
        ]
        return sorted(posixpath.relpath(p, top) for p in listed)

    def glob(self, folder, pattern):
        folder = self._norm(folder)
        return sorted(
            p
            for p in self._under(set(self.files) | set(self.links) | self.dirs, folder)
            if posixpath.relpath(p, folder).count("/") == pattern.count("/")
            and fnmatch.fnmatchcase(posixpath.relpath(p, folder), pattern)
        )

    def find_executable(self, name, search_path=None):
        return next(
            (
                posixpath.join(d, name)
                for d in (search_path or "").split(":")
                if d and posixpath.join(d, name) in self.files
            ),
            None,
        )

    # reading and writing
    def read_text(self, path):
        resolved = self._resolve(path)
        if resolved not in self.files:
            raise HeatmapError("cannot read %s: no such file" % path)
        return self.files[resolved]

    def write_text(self, path, text):
        resolved = self._resolve(path)
        self.make_dirs(posixpath.dirname(resolved))
        self.files[resolved] = text

    def append_text(self, path, text):
        resolved = self._resolve(path)
        if posixpath.dirname(resolved) not in self.dirs:
            raise FileNotFoundError(path)
        self.files[resolved] = self.files.get(resolved, "") + text

    def same_content(self, a, b):
        return self.read_text(a) == self.read_text(b)

    # creating, copying and removing
    def make_dirs(self, path):
        path = self._norm(path)
        while path not in self.dirs:
            self.dirs.add(path)
            path = posixpath.dirname(path)

    def copy_file(self, src, dst):
        self.write_text(dst, self.read_text(src))

    def copy_tree(self, src, dst):
        src, dst = self._resolve(src), self._norm(dst)
        if self.lexists(dst):
            raise FileExistsError(dst)
        self.make_dirs(dst)
        for d in self._under(self.dirs, src):
            self.make_dirs(dst + d[len(src) :])
        for store in (self.files, self.links):  # links are copied as links
            store.update({dst + p[len(src) :]: v for p, v in list(store.items()) if p.startswith(src + "/")})

    def remove(self, path):
        path = self._norm(path)
        if path in self.links:
            del self.links[path]
        elif path in self.files:
            del self.files[path]
        else:
            raise FileNotFoundError(path)

    def remove_tree(self, path):
        path = self._norm(path)
        if path not in self.dirs:
            raise FileNotFoundError(path)
        inside = (
            set(self._under(self.files, path)) | set(self._under(self.links, path)) | set(self._under(self.dirs, path))
        )
        self.files = {p: t for p, t in self.files.items() if p not in inside}
        self.links = {p: t for p, t in self.links.items() if p not in inside}
        self.dirs -= inside | {path}


def blob_id(text):
    """The id git gives a file's content (`git hash-object`)."""
    data = text.encode("utf-8", "surrogateescape")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()


def _patch(path, old, new):
    """The `git diff` section of one file."""
    header = "diff --git a/%s b/%s\n" % (path, path)
    header += "new file mode 100644\n" if old is None else "deleted file mode 100644\n" if new is None else ""
    return header + "".join(
        difflib.unified_diff(
            (old or "").splitlines(True),
            (new or "").splitlines(True),
            "/dev/null" if old is None else "a/" + path,
            "/dev/null" if new is None else "b/" + path,
        )
    )


class MemoryGit(Git):
    """A repository in memory with a linear history of whole-tree commits (`commit`), on branch `branch` at `root`. Its
    tracked files are the last commit's plus any `stage`d path. Revisions are HEAD, the branch, or a (short) sha."""

    def __init__(self, root="/repo", name="demo", branch="main"):
        self.root, self.name, self._branch, self.commits, self.staged = root, name, branch, [], set()

    def commit(self, subject, files=None, when=None, author="dev", remove=()):
        """Commit the last tree with `files` ({path: text}) written and `remove` deleted; returns the new sha."""
        tree = dict(self.commits[-1]["tree"]) if self.commits else {}
        tree.update(files or {})
        for path in remove:
            tree.pop(path, None)
        ts = int((when or dt.datetime.now(dt.timezone.utc)).timestamp())
        sha = hashlib.sha1(("%d\0%s\0%d" % (len(self.commits), subject, ts)).encode()).hexdigest()
        self.commits.append(
            {
                "sha": sha,
                "parent": self.commits[-1]["sha"] if self.commits else None,
                "author": author,
                "ts": ts,
                "subject": subject,
                "tree": tree,
            }
        )
        return sha

    def stage(self, path):
        """Track `path` without committing it (as `git add` does)."""
        self.staged.add(path)

    def _commit(self, rev):
        if self.commits and rev in ("HEAD", self._branch):
            return self.commits[-1]
        found = [c for c in self.commits if len(rev) >= 4 and c["sha"].startswith(rev)]
        if len(found) != 1:
            raise HeatmapError("git rev-parse failed: unknown revision %s" % rev)
        return found[0]

    def toplevel(self):
        return self.root

    def rev_parse(self, rev, short=False):
        sha = self._commit(rev)["sha"]
        return sha[:7] if short else sha

    def has_commit(self, rev):
        try:
            return bool(self._commit(rev))
        except HeatmapError:
            return False

    def merge_base(self, a, b):
        return min(self._commit(a), self._commit(b), key=self.commits.index)["sha"]

    def branch(self):
        return self._branch

    def repo_name(self):
        return self.name

    def ls_files(self):
        return sorted(set(self.commits[-1]["tree"] if self.commits else ()) | self.staged)

    def ls_tree(self, rev):
        return [(path, blob_id(text), len(text.encode())) for path, text in sorted(self._commit(rev)["tree"].items())]

    def read_blobs(self, blob_ids):
        known = {blob_id(text): text for c in self.commits for text in c["tree"].values()}
        return {b: known[b] for b in blob_ids if b in known}

    def diff_files(self, base, head):
        old, new = self._commit(base)["tree"], self._commit(head)["tree"]
        return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))

    def show(self, rev, path):
        return self._commit(rev)["tree"].get(path)

    def ensure_commit(self, sha, refspec):
        if not self.has_commit(sha):
            raise HeatmapError("commit %s is not available locally (checkout with fetch-depth: 0)" % sha)

    def diff(self, base, head, path=None):
        old, new = self._commit(base)["tree"], self._commit(head)["tree"]
        paths = self.diff_files(base, head) if path is None else [path]
        return "".join(_patch(p, old.get(p), new.get(p)) for p in paths if old.get(p) != new.get(p))

    def file_log(self, rev, path, limit=10):
        history = self.commits[: self.commits.index(self._commit(rev)) + 1]
        touched = [
            c for i, c in enumerate(history) if c["tree"].get(path) != (history[i - 1]["tree"].get(path) if i else None)
        ]
        return "\n".join(
            "%s %s %s: %s" % (c["sha"][:7], _day(c["ts"]), c["author"], c["subject"])
            for c in reversed(touched[-limit:])
        )

    def commit_patch(self, sha, paths):
        c = self._commit(sha)
        old = self._commit(c["parent"])["tree"] if c["parent"] else {}
        return "%s\n%s %s\n%s\n\n" % (c["sha"], c["author"], _day(c["ts"]), c["subject"]) + "".join(
            _patch(p, old.get(p), c["tree"].get(p)) for p in paths if old.get(p) != c["tree"].get(p)
        )

    def default_branch(self):
        return self._branch

    def first_parent_log(self, branch, since, until):
        self._commit(branch)
        return [
            (c["sha"], [c["parent"]] if c["parent"] else [], c["author"], c["ts"], c["subject"])
            for c in reversed(self.commits)
            if since <= _day(c["ts"]) <= until
        ]
