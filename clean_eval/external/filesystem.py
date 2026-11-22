"""The file system: every file and folder clean-eval reads, writes, copies, compares or resolves, and the path strings it
builds. Text is read with undecodable bytes replaced; a read that fails raises HeatmapError."""
import filecmp
import os
import shutil
from pathlib import Path

from clean_eval.domain.errors import HeatmapError
from clean_eval.use_cases.interfaces import FileSystem


class FileSystemGateway(FileSystem):
    sep = os.sep

    # ---------------------------------------------------------------- paths (strings only, nothing is touched)

    def join(self, *parts):
        return os.path.join(*parts)

    def parent(self, path):
        return os.path.dirname(path)

    def name(self, path):
        return os.path.basename(path)

    def relative(self, path, start):
        return os.path.relpath(path, start)

    # ---------------------------------------------------------------- paths resolved against the system

    def absolute(self, path):
        return os.path.abspath(path)

    def real_path(self, path):
        """The path with every symlink resolved."""
        return os.path.realpath(path)

    def expand_user(self, path):
        return os.path.expanduser(path)

    def cwd(self):
        return os.getcwd()

    # ---------------------------------------------------------------- what is there

    def exists(self, path):
        return os.path.exists(path)

    def lexists(self, path):
        """Whether anything is at `path`, a dangling symlink included."""
        return os.path.lexists(path)

    def is_file(self, path):
        return os.path.isfile(path)

    def is_dir(self, path):
        return os.path.isdir(path)

    def is_link(self, path):
        return os.path.islink(path)

    def size(self, path):
        return os.path.getsize(path)

    def files_under(self, top):
        """Every file below `top`, as paths relative to it (a symlinked folder is neither followed nor listed)."""
        out = []
        for root, _, names in os.walk(top) if os.path.isdir(top) else ():
            out += [os.path.relpath(os.path.join(root, name), top) for name in names]
        return out

    def glob(self, folder, pattern):
        """The paths in `folder` that match `pattern`, sorted."""
        return [str(p) for p in sorted(Path(folder).glob(pattern))]

    def find_executable(self, name, search_path=None):
        """Where `name` runs from: the first match on `search_path` (os.pathsep-separated), or None."""
        return shutil.which(name, path=search_path)

    # ---------------------------------------------------------------- reading and writing

    def read_text(self, path):
        try:
            return Path(path).read_text(errors="replace")
        except OSError as e:
            raise HeatmapError("cannot read %s: %s" % (path, e))

    def write_text(self, path, text):
        """Write the file, making its folders."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text)

    def append_text(self, path, text):
        with open(path, "a") as f:
            f.write(text)

    def same_content(self, a, b):
        return filecmp.cmp(a, b, shallow=False)

    # ---------------------------------------------------------------- creating, copying and removing

    def make_dirs(self, path):
        os.makedirs(path, exist_ok=True)

    def copy_file(self, src, dst):
        shutil.copyfile(src, dst)

    def copy_tree(self, src, dst):
        """Copy a folder, keeping symlinks as links."""
        shutil.copytree(src, dst, symlinks=True)

    def remove(self, path):
        """Remove a file or a link (a link's target is left alone)."""
        os.remove(path)

    def remove_tree(self, path):
        shutil.rmtree(path)
