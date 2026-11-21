"""The git CLI, as the rest of the adaptor layer needs it. Paths are handled raw (NUL-delimited or unquoted), so any
filename survives; nothing in the repository is executed."""
import os
import re
import subprocess

from ...domain.errors import HeatmapError

_C_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def unquote_path(p):
    """Undo git's C-style path quoting ("src/we\\"ird.py", "r\\303\\251sum\\303\\251.py")."""
    if len(p) < 2 or not (p.startswith('"') and p.endswith('"')):
        return p
    body, out, i = p[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i]
        if ch != "\\" or i + 1 >= len(body):
            out += ch.encode("utf-8", "surrogateescape")
            i += 1
        elif body[i + 1] in _C_ESCAPES:
            out.append(_C_ESCAPES[body[i + 1]])
            i += 2
        elif re.match(r"[0-7]{3}", body[i + 1:i + 4]):
            out.append(int(body[i + 1:i + 4], 8))
            i += 4
        else:
            out += ch.encode()
            i += 1
    return out.decode("utf-8", "surrogateescape")


class Git:
    def __init__(self, root):
        self.root = root

    def run(self, *args, check=True):
        r = subprocess.run(["git", "-c", "core.quotePath=false", "-C", self.root, *args],
                           capture_output=True, text=True, errors="surrogateescape")
        if check and r.returncode != 0:
            raise HeatmapError("git %s failed: %s" % (" ".join(args[:3]), r.stderr.strip()[-300:]))
        return r.stdout

    def toplevel(self):
        return self.run("rev-parse", "--show-toplevel").strip()

    def rev_parse(self, rev, short=False):
        return self.run("rev-parse", *(["--short"] if short else []), "--verify", rev + "^{commit}").strip()

    def has_commit(self, rev):
        return subprocess.run(["git", "-C", self.root, "cat-file", "-e", rev + "^{commit}"], capture_output=True).returncode == 0

    def merge_base(self, a, b):
        return self.run("merge-base", a, b).strip()

    def branch(self):
        name = self.run("rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
        return name if name and name != "HEAD" else ""

    def repo_name(self):
        url = self.run("config", "--get", "remote.origin.url", check=False).strip()
        name = re.sub(r"\.git$", "", url.rstrip("/")).split("/")[-1].split(":")[-1] if url else ""
        return name or os.path.basename(self.toplevel())

    def ls_files(self):
        return [p for p in self.run("ls-files", "-z").split("\0") if p]

    def ls_tree(self, rev):
        """[(path, blob id, size)] of the files at `rev`."""
        out = []
        for entry in self.run("ls-tree", "-r", "-z", "--long", rev).split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            mode, kind, blob, size = meta.split()
            if kind == "blob" and size.isdigit():
                out.append((path, blob, int(size)))
        return out

    def read_blobs(self, blob_ids):
        """{blob id: text} for the given blobs, read through one `git cat-file --batch` process."""
        ids = list(dict.fromkeys(blob_ids))
        if not ids:
            return {}
        p = subprocess.run(["git", "-C", self.root, "cat-file", "--batch"], input="\n".join(ids).encode() + b"\n",
                           capture_output=True)
        if p.returncode != 0:
            raise HeatmapError("git cat-file failed: %s" % p.stderr.decode(errors="replace")[-300:])
        out, data, pos = {}, p.stdout, 0
        for _ in ids:
            nl = data.index(b"\n", pos)
            header = data[pos:nl].decode().split()
            pos = nl + 1
            if len(header) < 3 or header[1] == "missing":
                continue
            size = int(header[2])
            out[header[0]] = data[pos:pos + size].decode("utf-8", "ignore")
            pos += size + 1
        return out

    def diff_files(self, base, head):
        """Paths changed between two revisions (raw, NUL-delimited)."""
        return [p for p in self.run("diff", "--name-only", "-z", base, head).split("\0") if p]

    def show(self, rev, path):
        """Text of `path` at `rev`, or None when it does not exist there."""
        r = subprocess.run(["git", "-C", self.root, "show", "%s:%s" % (rev, path)], capture_output=True)
        return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None
