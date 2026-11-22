"""The file-system gateway (external/filesystem.py), on temporary folders; and the contract it shares with the use
cases' MemoryFileSystem (tests/use_cases/fakes.py): the same checks run against both."""
import os
import shutil
import tempfile
import unittest

from tests.helpers import make_executable, read, write
from tests.use_cases.fakes import MemoryFileSystem

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.filesystem import FileSystemGateway


class FileSystemGatewayTest(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.fs = FileSystemGateway()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    def test_reading_and_writing(self):
        self.fs.write_text(self.path("a", "b", "c.txt"), "hi")            # folders are made
        self.assertEqual(self.fs.read_text(self.path("a", "b", "c.txt")), "hi")
        with open(self.path("bad.txt"), "wb") as f:
            f.write(b"ok \xff\xfe")
        self.assertEqual(self.fs.read_text(self.path("bad.txt")), "ok ��")  # undecodable bytes replaced
        with self.assertRaises(HeatmapError):
            self.fs.read_text(self.path("missing.txt"))
        self.fs.append_text(self.path("out.txt"), "a=1\n")
        self.fs.append_text(self.path("out.txt"), "b=2\n")
        self.assertEqual(read(self.path("out.txt")), "a=1\nb=2\n")

    def test_listing_copying_and_comparing(self):
        write(self.path("t", "x.py"), "x")
        write(self.path("t", "sub", "y.py"), "y")
        os.symlink(self.path("t", "sub"), self.path("t", "linked"))     # a symlinked folder is neither followed nor listed
        self.assertEqual(sorted(self.fs.files_under(self.path("t"))), sorted(["x.py", os.path.join("sub", "y.py")]))
        self.assertEqual(self.fs.files_under(self.path("nope")), [])
        self.fs.copy_tree(self.path("t"), self.path("copy"))
        self.assertTrue(self.fs.is_link(self.path("copy", "linked")))  # links stay links
        self.assertTrue(self.fs.same_content(self.path("t", "x.py"), self.path("copy", "x.py")))
        self.assertEqual(self.fs.glob(self.path("t"), "*.py"), [self.path("t", "x.py")])
        self.fs.remove_tree(self.path("copy"))
        self.assertFalse(self.fs.exists(self.path("copy")))

    def test_resolving_links(self):
        write(self.path("target.txt"), "t")
        os.symlink(self.path("target.txt"), self.path("link.txt"))
        self.assertEqual(self.fs.real_path(self.path("link.txt")), self.path("target.txt"))
        self.fs.remove(self.path("link.txt"))                            # the link goes, its target stays
        self.assertEqual((self.fs.lexists(self.path("link.txt")), self.fs.exists(self.path("target.txt"))), (False, True))

    def test_finding_executables(self):
        bin_dir = self.path("bin")
        os.makedirs(bin_dir)
        make_executable(os.path.join(bin_dir, "tool"), "print(1)\n")
        self.assertEqual(self.fs.find_executable("tool", bin_dir), os.path.join(bin_dir, "tool"))
        self.assertIsNone(self.fs.find_executable("tool", self.path("empty")))


class FileSystemContract:
    """Files, folders and links under `self.root`, made through the interface (links through `self.link`)."""

    def path(self, *parts):
        return self.fs.join(self.root, *parts)

    def test_reading_and_writing(self):
        self.fs.write_text(self.path("a", "b.txt"), "hi")  # folders are made
        self.assertEqual(self.fs.read_text(self.path("a", "b.txt")), "hi")
        self.assertEqual((self.fs.is_file(self.path("a", "b.txt")), self.fs.is_dir(self.path("a"))), (True, True))
        self.assertEqual((self.fs.exists(self.path("a", "b.txt")), self.fs.size(self.path("a", "b.txt"))), (True, 2))
        with self.assertRaises(HeatmapError):
            self.fs.read_text(self.path("missing.txt"))
        self.fs.append_text(self.path("a", "b.txt"), "!")
        self.assertEqual(self.fs.read_text(self.path("a", "b.txt")), "hi!")
        self.fs.copy_file(self.path("a", "b.txt"), self.path("a", "c.txt"))
        self.assertTrue(self.fs.same_content(self.path("a", "b.txt"), self.path("a", "c.txt")))

    def test_paths(self):
        self.assertEqual(self.fs.relative(self.path("a", "b.txt"), self.root), "a/b.txt")
        self.assertEqual((self.fs.parent(self.path("a", "b.txt")), self.fs.name(self.path("a", "b.txt"))),
                         (self.path("a"), "b.txt"))
        self.assertEqual(self.fs.absolute(self.path("a", "..", "b")), self.path("b"))

    def test_listing(self):
        for rel in ("x.py", "sub/y.py", "sub/deeper/z.xml", "coverage.xml"):
            self.fs.write_text(self.path(*rel.split("/")), rel)
        self.assertEqual(sorted(self.fs.files_under(self.root)), ["coverage.xml", "sub/deeper/z.xml", "sub/y.py", "x.py"])
        self.assertEqual(self.fs.files_under(self.path("nope")), [])
        self.assertEqual(self.fs.glob(self.root, "*.py"), [self.path("x.py")])
        self.assertEqual(self.fs.glob(self.root, "*/*.py"), [self.path("sub", "y.py")])  # one level, not two

    def test_links(self):
        self.fs.write_text(self.path("target.txt"), "t")
        self.link(self.path("link.txt"), self.path("target.txt"))
        self.assertTrue(self.fs.is_link(self.path("link.txt")))
        self.assertEqual(self.fs.real_path(self.path("link.txt")), self.path("target.txt"))
        self.assertEqual(self.fs.read_text(self.path("link.txt")), "t")
        self.fs.remove(self.path("link.txt"))  # the link goes, its target stays
        self.assertEqual((self.fs.lexists(self.path("link.txt")), self.fs.exists(self.path("target.txt"))), (False, True))
        self.link(self.path("dangling.txt"), self.path("nothing.txt"))
        self.assertEqual((self.fs.lexists(self.path("dangling.txt")), self.fs.exists(self.path("dangling.txt"))), (True, False))

    def test_copying_and_removing_folders(self):
        self.fs.write_text(self.path("t", "x.txt"), "x")
        self.link(self.path("t", "l.txt"), self.path("t", "x.txt"))
        self.fs.copy_tree(self.path("t"), self.path("copy"))
        self.assertTrue(self.fs.is_link(self.path("copy", "l.txt")))  # links stay links
        self.assertEqual(self.fs.read_text(self.path("copy", "x.txt")), "x")
        self.fs.remove_tree(self.path("copy"))
        self.assertFalse(self.fs.lexists(self.path("copy")))


class MemoryFileSystemContract(FileSystemContract, unittest.TestCase):
    def setUp(self):
        self.fs, self.root = MemoryFileSystem(), "/tmp/contract"
        self.link = self.fs.link


class FileSystemGatewayContract(FileSystemContract, unittest.TestCase):
    def setUp(self):
        self.fs, self.root = FileSystemGateway(), os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.link = lambda path, target: os.symlink(target, path)


if __name__ == "__main__":
    unittest.main()
