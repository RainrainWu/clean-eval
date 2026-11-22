"""The file-system gateway (external/filesystem.py), on temporary folders; and the contract it shares with the use
cases' MemoryFileSystem (tests/use_cases/fakes.py): the contract checks run against both."""

import os

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.filesystem import FileSystemGateway
from tests.helpers import make_executable, read, write
from tests.use_cases.fakes import MemoryFileSystem


class Folder:
    """A folder to work under, on a FileSystem, with a way to make symlinks there (the interface has none)."""

    def __init__(self, fs, root, link):
        self.fs, self.root, self.link = fs, root, link

    def path(self, *parts):
        return self.fs.join(self.root, *parts)


def real_folder(tmp_path):
    return Folder(FileSystemGateway(), os.path.realpath(tmp_path), lambda path, target: os.symlink(target, path))


def memory_folder(tmp_path):
    fs = MemoryFileSystem()
    return Folder(fs, "/tmp/contract", fs.link)


@pytest.fixture
def real(tmp_path):
    """A temporary folder on the real file system, through FileSystemGateway."""
    return real_folder(tmp_path)


def test_undecodable_bytes_missing_files_and_appending(real):
    real.fs.write_text(real.path("a", "b", "c.txt"), "hi")  # folders are made
    assert real.fs.read_text(real.path("a", "b", "c.txt")) == "hi"
    with open(real.path("bad.txt"), "wb") as f:
        f.write(b"ok \xff\xfe")
    assert real.fs.read_text(real.path("bad.txt")) == "ok ��"  # undecodable bytes replaced
    with pytest.raises(HeatmapError):
        real.fs.read_text(real.path("missing.txt"))
    real.fs.append_text(real.path("out.txt"), "a=1\n")
    real.fs.append_text(real.path("out.txt"), "b=2\n")
    assert read(real.path("out.txt")) == "a=1\nb=2\n"


def test_listing_copying_and_comparing(real):
    write(real.path("t", "x.py"), "x")
    write(real.path("t", "sub", "y.py"), "y")
    os.symlink(real.path("t", "sub"), real.path("t", "linked"))  # a symlinked folder is neither followed nor listed
    assert sorted(real.fs.files_under(real.path("t"))) == sorted(["x.py", os.path.join("sub", "y.py")])
    assert real.fs.files_under(real.path("nope")) == []
    real.fs.copy_tree(real.path("t"), real.path("copy"))
    assert real.fs.is_link(real.path("copy", "linked"))  # links stay links
    assert real.fs.same_content(real.path("t", "x.py"), real.path("copy", "x.py"))
    assert real.fs.glob(real.path("t"), "*.py") == [real.path("t", "x.py")]
    real.fs.remove_tree(real.path("copy"))
    assert not real.fs.exists(real.path("copy"))


def test_resolving_links(real):
    write(real.path("target.txt"), "t")
    os.symlink(real.path("target.txt"), real.path("link.txt"))
    assert real.fs.real_path(real.path("link.txt")) == real.path("target.txt")
    real.fs.remove(real.path("link.txt"))  # the link goes, its target stays
    assert (real.fs.lexists(real.path("link.txt")), real.fs.exists(real.path("target.txt"))) == (False, True)


def test_finding_executables(real):
    bin_dir = real.path("bin")
    os.makedirs(bin_dir)
    make_executable(os.path.join(bin_dir, "tool"), "print(1)\n")
    assert real.fs.find_executable("tool", bin_dir) == os.path.join(bin_dir, "tool")
    assert real.fs.find_executable("tool", real.path("empty")) is None


# The contract: every check below runs on a MemoryFileSystem and on the real file system.


@pytest.fixture(params=[memory_folder, real_folder], ids=["MemoryFileSystem", "FileSystemGateway"])
def folder(request, tmp_path):
    return request.param(tmp_path)


def test_reading_and_writing(folder):
    folder.fs.write_text(folder.path("a", "b.txt"), "hi")  # folders are made
    assert folder.fs.read_text(folder.path("a", "b.txt")) == "hi"
    assert (folder.fs.is_file(folder.path("a", "b.txt")), folder.fs.is_dir(folder.path("a"))) == (True, True)
    assert (folder.fs.exists(folder.path("a", "b.txt")), folder.fs.size(folder.path("a", "b.txt"))) == (True, 2)
    with pytest.raises(HeatmapError):
        folder.fs.read_text(folder.path("missing.txt"))
    folder.fs.append_text(folder.path("a", "b.txt"), "!")
    assert folder.fs.read_text(folder.path("a", "b.txt")) == "hi!"
    folder.fs.copy_file(folder.path("a", "b.txt"), folder.path("a", "c.txt"))
    assert folder.fs.same_content(folder.path("a", "b.txt"), folder.path("a", "c.txt"))


def test_paths(folder):
    assert folder.fs.relative(folder.path("a", "b.txt"), folder.root) == "a/b.txt"
    assert (folder.fs.parent(folder.path("a", "b.txt")), folder.fs.name(folder.path("a", "b.txt"))) == (
        folder.path("a"),
        "b.txt",
    )
    assert folder.fs.absolute(folder.path("a", "..", "b")) == folder.path("b")


def test_listing(folder):
    for rel in ("x.py", "sub/y.py", "sub/deeper/z.xml", "coverage.xml"):
        folder.fs.write_text(folder.path(*rel.split("/")), rel)
    assert sorted(folder.fs.files_under(folder.root)) == ["coverage.xml", "sub/deeper/z.xml", "sub/y.py", "x.py"]
    assert folder.fs.files_under(folder.path("nope")) == []
    assert folder.fs.glob(folder.root, "*.py") == [folder.path("x.py")]
    assert folder.fs.glob(folder.root, "*/*.py") == [folder.path("sub", "y.py")]  # one level, not two


def test_links(folder):
    folder.fs.write_text(folder.path("target.txt"), "t")
    folder.link(folder.path("link.txt"), folder.path("target.txt"))
    assert folder.fs.is_link(folder.path("link.txt"))
    assert folder.fs.real_path(folder.path("link.txt")) == folder.path("target.txt")
    assert folder.fs.read_text(folder.path("link.txt")) == "t"
    folder.fs.remove(folder.path("link.txt"))  # the link goes, its target stays
    assert (folder.fs.lexists(folder.path("link.txt")), folder.fs.exists(folder.path("target.txt"))) == (False, True)
    folder.link(folder.path("dangling.txt"), folder.path("nothing.txt"))
    assert (folder.fs.lexists(folder.path("dangling.txt")), folder.fs.exists(folder.path("dangling.txt"))) == (
        True,
        False,
    )


def test_copying_and_removing_folders(folder):
    folder.fs.write_text(folder.path("t", "x.txt"), "x")
    folder.link(folder.path("t", "l.txt"), folder.path("t", "x.txt"))
    folder.fs.copy_tree(folder.path("t"), folder.path("copy"))
    assert folder.fs.is_link(folder.path("copy", "l.txt"))  # links stay links
    assert folder.fs.read_text(folder.path("copy", "x.txt")) == "x"
    folder.fs.remove_tree(folder.path("copy"))
    assert not folder.fs.lexists(folder.path("copy"))
