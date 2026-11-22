"""In-memory implementations of two of the use cases' interfaces (clean_eval/use_cases/interfaces.py): a file system
and a git repository. They behave as the interfaces document: the contract tests in tests/external/test_filesystem.py
and test_git.py run them and the real gateways through the same checks. The use cases' tests get them behind MagicMocks
(the `fs` and `git` fixtures of tests/use_cases/conftest.py), which record every call."""

import datetime as dt
import difflib
import fnmatch
import hashlib
import posixpath

from clean_eval.domain.errors import HeatmapError
from clean_eval.use_cases.interfaces import FileSystem, Git


class MemoryFileSystem(FileSystem):
    """Files, folders and symlinks in memory, at POSIX paths (relative ones resolve against `cwd`)."""

    sep = "/"

    def __init__(
        self,
        files=None,
        cwd="/work",
    ):
        self.files, self.links, self.dirs, self._cwd = {}, {}, {"/"}, cwd
        for path, text in (files or {}).items():
            self.write_text(path, text)

    def link(
        self,
        path,
        target,
    ):
        """Make `path` a symlink to `target`."""
        self.make_dirs(posixpath.dirname(self.normalize(path)))
        self.links[self.normalize(path)] = self.normalize(target)

    def normalize(
        self,
        path,
    ):
        return posixpath.normpath(posixpath.join(self._cwd, path))

    def resolve(
        self,
        path,
    ):
        current = "/"
        for part in self.normalize(path).strip("/").split("/"):
            current, hops = posixpath.join(current, part), 0
            while current in self.links:
                current, hops = self.links[current], hops + 1
                if hops > 40:
                    raise HeatmapError("too many levels of symlinks in %s" % path)
        return current

    def paths_under(
        self,
        paths,
        top,
    ):
        return [p for p in paths if p.startswith(top.rstrip("/") + "/")]

    # paths
    def join(
        self,
        *parts,
    ):
        return posixpath.join(*parts)

    def parent(
        self,
        path,
    ):
        return posixpath.dirname(path)

    def name(
        self,
        path,
    ):
        return posixpath.basename(path)

    def relative(
        self,
        path,
        start,
    ):
        return posixpath.relpath(self.normalize(path), self.normalize(start))

    def absolute(
        self,
        path,
    ):
        return self.normalize(path)

    def real_path(
        self,
        path,
    ):
        return self.resolve(path)

    def expand_user(
        self,
        path,
    ):
        return "/home/user" + path[1:] if path == "~" or path.startswith("~/") else path

    def cwd(
        self,
    ):
        return self._cwd

    # what is there
    def exists(
        self,
        path,
    ):
        resolved = self.resolve(path)
        return resolved in self.files or resolved in self.dirs

    def lexists(
        self,
        path,
    ):
        return self.normalize(path) in self.links or self.exists(path)

    def is_file(
        self,
        path,
    ):
        return self.resolve(path) in self.files

    def is_dir(
        self,
        path,
    ):
        return self.resolve(path) in self.dirs

    def is_link(
        self,
        path,
    ):
        return self.normalize(path) in self.links

    def size(
        self,
        path,
    ):
        return len(self.read_text(path).encode())

    def files_under(
        self,
        top,
    ):
        top = self.normalize(top)
        if top not in self.dirs:
            return []
        listed = self.paths_under(self.files, top) + [
            p for p in self.paths_under(self.links, top) if self.links[p] not in self.dirs
        ]
        return sorted(posixpath.relpath(p, top) for p in listed)

    def glob(
        self,
        folder,
        pattern,
    ):
        folder = self.normalize(folder)
        return sorted(
            p
            for p in self.paths_under(set(self.files) | set(self.links) | self.dirs, folder)
            if posixpath.relpath(p, folder).count("/") == pattern.count("/")
            and fnmatch.fnmatchcase(posixpath.relpath(p, folder), pattern)
        )

    def find_executable(
        self,
        name,
        search_path=None,
    ):
        return next(
            (
                posixpath.join(d, name)
                for d in (search_path or "").split(":")
                if d and posixpath.join(d, name) in self.files
            ),
            None,
        )

    # reading and writing
    def read_text(
        self,
        path,
    ):
        resolved = self.resolve(path)
        if resolved not in self.files:
            raise HeatmapError("cannot read %s: no such file" % path)
        return self.files[resolved]

    def write_text(
        self,
        path,
        text,
    ):
        resolved = self.resolve(path)
        self.make_dirs(posixpath.dirname(resolved))
        self.files[resolved] = text

    def append_text(
        self,
        path,
        text,
    ):
        resolved = self.resolve(path)
        if posixpath.dirname(resolved) not in self.dirs:
            raise FileNotFoundError(path)
        self.files[resolved] = self.files.get(resolved, "") + text

    def same_content(
        self,
        first_path,
        second_path,
    ):
        return self.read_text(first_path) == self.read_text(second_path)

    # creating, copying and removing
    def make_dirs(
        self,
        path,
    ):
        path = self.normalize(path)
        while path not in self.dirs:
            self.dirs.add(path)
            path = posixpath.dirname(path)

    def copy_file(
        self,
        src,
        dst,
    ):
        self.write_text(dst, self.read_text(src))

    def copy_tree(
        self,
        src,
        dst,
    ):
        src, dst = self.resolve(src), self.normalize(dst)
        if self.lexists(dst):
            raise FileExistsError(dst)
        self.make_dirs(dst)
        for d in self.paths_under(self.dirs, src):
            self.make_dirs(dst + d[len(src) :])
        for store in (self.files, self.links):  # links are copied as links
            store.update({dst + p[len(src) :]: v for p, v in list(store.items()) if p.startswith(src + "/")})

    def remove(
        self,
        path,
    ):
        path = self.normalize(path)
        if path in self.links:
            del self.links[path]
        elif path in self.files:
            del self.files[path]
        else:
            raise FileNotFoundError(path)

    def remove_tree(
        self,
        path,
    ):
        path = self.normalize(path)
        if path not in self.dirs:
            raise FileNotFoundError(path)
        inside = (
            set(self.paths_under(self.files, path))
            | set(self.paths_under(self.links, path))
            | set(self.paths_under(self.dirs, path))
        )
        self.files = {p: t for p, t in self.files.items() if p not in inside}
        self.links = {p: t for p, t in self.links.items() if p not in inside}
        self.dirs -= inside | {path}


class MemoryGit(Git):
    """A repository in memory with a linear history of whole-tree commits (`commit`), on branch `branch` at `root`. Its
    tracked files are the last commit's plus any `stage`d path. Revisions are HEAD, the branch, or a (short) sha."""

    def __init__(
        self,
        root="/repo",
        name="demo",
        branch="main",
    ):
        self.root, self.name, self._branch, self.commits, self.staged = root, name, branch, [], set()

    @staticmethod
    def blob_id(
        text,
    ):
        """The id git gives a file's content (`git hash-object`)."""
        data = text.encode("utf-8", "surrogateescape")
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    @staticmethod
    def day_of(
        ts,
    ):
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()

    @staticmethod
    def file_patch(
        path,
        old,
        new,
    ):
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

    def commit(
        self,
        subject,
        files=None,
        when=None,
        author="dev",
        remove=(),
    ):
        """Commit the last tree with `files` ({path: text}) written and `remove` deleted, at `when` (a datetime, or an
        ISO date or time, UTC unless it says otherwise; default now); returns the new sha."""
        tree = dict(self.commits[-1]["tree"]) if self.commits else {}
        tree.update(files or {})
        for path in remove:
            tree.pop(path, None)
        if isinstance(when, str):
            when = dt.datetime.fromisoformat(when)
            when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
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

    def stage(
        self,
        path,
    ):
        """Track `path` without committing it (as `git add` does)."""
        self.staged.add(path)

    def find_commit(
        self,
        rev,
    ):
        if self.commits and rev in ("HEAD", self._branch):
            return self.commits[-1]
        found = [c for c in self.commits if len(rev) >= 4 and c["sha"].startswith(rev)]
        if len(found) != 1:
            raise HeatmapError("git rev-parse failed: unknown revision %s" % rev)
        return found[0]

    def toplevel(
        self,
    ):
        return self.root

    def is_shallow(
        self,
    ):
        return False  # a memory repository holds its whole history

    def rev_parse(
        self,
        rev,
        short=False,
    ):
        sha = self.find_commit(rev)["sha"]
        return sha[:7] if short else sha

    def has_commit(
        self,
        rev,
    ):
        try:
            return bool(self.find_commit(rev))
        except HeatmapError:
            return False

    def merge_base(
        self,
        first_rev,
        second_rev,
    ):
        return min(self.find_commit(first_rev), self.find_commit(second_rev), key=self.commits.index)["sha"]

    def branch(
        self,
    ):
        return self._branch

    def repo_name(
        self,
    ):
        return self.name

    def ls_files(
        self,
    ):
        return sorted(set(self.commits[-1]["tree"] if self.commits else ()) | self.staged)

    def ls_tree(
        self,
        rev,
    ):
        return [
            (path, self.blob_id(text), len(text.encode()))
            for path, text in sorted(self.find_commit(rev)["tree"].items())
        ]

    def read_blobs(
        self,
        blob_ids,
    ):
        known = {self.blob_id(text): text for c in self.commits for text in c["tree"].values()}
        return {b: known[b] for b in blob_ids if b in known}

    def diff_files(
        self,
        base,
        head,
    ):
        old, new = self.find_commit(base)["tree"], self.find_commit(head)["tree"]
        return sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p))

    def show(
        self,
        rev,
        path,
    ):
        return self.find_commit(rev)["tree"].get(path)

    def ensure_commit(
        self,
        sha,
        refspec,
    ):
        if not self.has_commit(sha):
            raise HeatmapError("commit %s is not available locally (checkout with fetch-depth: 0)" % sha)

    def diff(
        self,
        base,
        head,
        path=None,
    ):
        old, new = self.find_commit(base)["tree"], self.find_commit(head)["tree"]
        paths = self.diff_files(base, head) if path is None else [path]
        return "".join(self.file_patch(p, old.get(p), new.get(p)) for p in paths if old.get(p) != new.get(p))

    def file_log(
        self,
        rev,
        path,
        limit=10,
    ):
        history = self.commits[: self.commits.index(self.find_commit(rev)) + 1]
        touched = [
            c for i, c in enumerate(history) if c["tree"].get(path) != (history[i - 1]["tree"].get(path) if i else None)
        ]
        return "\n".join(
            "%s %s %s: %s" % (c["sha"][:7], self.day_of(c["ts"]), c["author"], c["subject"])
            for c in reversed(touched[-limit:])
        )

    def commit_patch(
        self,
        sha,
        paths,
    ):
        c = self.find_commit(sha)
        old = self.find_commit(c["parent"])["tree"] if c["parent"] else {}
        return "%s\n%s %s\n%s\n\n" % (c["sha"], c["author"], self.day_of(c["ts"]), c["subject"]) + "".join(
            self.file_patch(p, old.get(p), c["tree"].get(p)) for p in paths if old.get(p) != c["tree"].get(p)
        )

    def default_branch(
        self,
    ):
        return self._branch

    def first_parent_log(
        self,
        branch,
        since,
        until,
    ):
        self.find_commit(branch)
        return [
            (c["sha"], [c["parent"]] if c["parent"] else [], c["author"], c["ts"], c["subject"])
            for c in reversed(self.commits)
            if since <= self.day_of(c["ts"]) <= until
        ]
