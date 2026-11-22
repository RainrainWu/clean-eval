"""The file-system gateway (external/filesystem.py) on real temporary folders; and the contract it shares with the use
cases' MemoryFileSystem (tests/use_cases/fakes.py): the contract checks run on both. `fs` holds `files` and `links`
under `root`."""

from pathlib import Path

import pytest

from clean_eval.domain.errors import HeatmapError
from tests.helpers import FakeBin


class TestFileSystemGateway:
    """What the real file system adds: undecodable bytes, appending to a file, a symlinked folder neither followed nor
    listed, links resolved and removed, and executables found on a search path."""

    def test_writing_makes_the_folders(
        self,
        fs,
        root,
    ):
        fs.write_text(fs.join(root, "a", "b", "c.txt"), "hi")
        assert fs.read_text(fs.join(root, "a", "b", "c.txt")) == "hi"

    def test_undecodable_bytes_are_replaced(
        self,
        fs,
        root,
    ):
        Path(
            root,
            "bad.txt",
        ).write_bytes(b"ok \xff\xfe")
        assert fs.read_text(fs.join(root, "bad.txt")) == "ok ��"

    def test_a_missing_file_cannot_be_read(
        self,
        fs,
        root,
    ):
        with pytest.raises(HeatmapError):
            fs.read_text(fs.join(root, "missing.txt"))

    def test_appending_adds_to_the_end(
        self,
        fs,
        root,
    ):
        fs.append_text(fs.join(root, "out.txt"), "a=1\n")
        fs.append_text(fs.join(root, "out.txt"), "b=2\n")
        assert (
            Path(
                root,
                "out.txt",
            ).read_text()
            == "a=1\nb=2\n"
        )

    @pytest.mark.parametrize(
        ("files", "links"),
        [({"t/x.py": "x", "t/sub/y.py": "y"}, {"t/linked": "t/sub"})],
        indirect=True,
        ids=["a symlinked folder"],
    )
    def test_listing_copying_and_comparing(
        self,
        fs,
        root,
    ):
        assert sorted(fs.files_under(fs.join(root, "t"))) == [
            str(
                Path(
                    "sub",
                    "y.py",
                )
            ),
            "x.py",
        ]  # not the linked one
        assert fs.files_under(fs.join(root, "nope")) == []
        fs.copy_tree(fs.join(root, "t"), fs.join(root, "copy"))
        assert fs.is_link(fs.join(root, "copy", "linked"))  # links stay links
        assert fs.same_content(fs.join(root, "t", "x.py"), fs.join(root, "copy", "x.py"))
        assert fs.glob(fs.join(root, "t"), "*.py") == [fs.join(root, "t", "x.py")]
        fs.remove_tree(fs.join(root, "copy"))
        assert not fs.exists(fs.join(root, "copy"))

    @pytest.mark.parametrize(
        ("files", "links"), [({"target.txt": "t"}, {"link.txt": "target.txt"})], indirect=True, ids=["a link"]
    )
    def test_a_link_resolves_to_its_target_and_is_removed_alone(
        self,
        fs,
        root,
    ):
        assert fs.real_path(fs.join(root, "link.txt")) == fs.join(root, "target.txt")
        fs.remove(fs.join(root, "link.txt"))  # the link goes, its target stays
        assert (fs.lexists(fs.join(root, "link.txt")), fs.exists(fs.join(root, "target.txt"))) == (False, True)

    def test_an_executable_is_found_on_its_search_path(
        self,
        fs,
        root,
    ):
        Path(
            root,
            "bin",
        ).mkdir()
        FakeBin.make_executable(fs.join(root, "bin", "tool"), "print(1)\n")
        assert fs.find_executable("tool", fs.join(root, "bin")) == fs.join(root, "bin", "tool")
        assert fs.find_executable("tool", fs.join(root, "empty")) is None


@pytest.mark.parametrize("fs", ["MemoryFileSystem", "FileSystemGateway"], indirect=True)
class TestMemoryFileSystemAgreesWithTheGateway:
    """The contract: every check runs on the in-memory MemoryFileSystem and on FileSystemGateway, so the use cases'
    tests run on a fake that behaves like the real thing."""

    def test_reading_and_writing(
        self,
        fs,
        root,
    ):
        fs.write_text(fs.join(root, "a", "b.txt"), "hi")  # folders are made
        assert fs.read_text(fs.join(root, "a", "b.txt")) == "hi"
        assert (fs.is_file(fs.join(root, "a", "b.txt")), fs.is_dir(fs.join(root, "a"))) == (True, True)
        assert (fs.exists(fs.join(root, "a", "b.txt")), fs.size(fs.join(root, "a", "b.txt"))) == (True, 2)
        with pytest.raises(HeatmapError):
            fs.read_text(fs.join(root, "missing.txt"))
        fs.append_text(fs.join(root, "a", "b.txt"), "!")
        assert fs.read_text(fs.join(root, "a", "b.txt")) == "hi!"
        fs.copy_file(fs.join(root, "a", "b.txt"), fs.join(root, "a", "c.txt"))
        assert fs.same_content(fs.join(root, "a", "b.txt"), fs.join(root, "a", "c.txt"))

    def test_paths(
        self,
        fs,
        root,
    ):
        assert fs.relative(fs.join(root, "a", "b.txt"), root) == "a/b.txt"
        assert (fs.parent(fs.join(root, "a", "b.txt")), fs.name(fs.join(root, "a", "b.txt"))) == (
            fs.join(root, "a"),
            "b.txt",
        )
        assert fs.absolute(fs.join(root, "a", "..", "b")) == fs.join(root, "b")

    @pytest.mark.parametrize(
        "files",
        [
            {
                "x.py": "x.py",
                "sub/y.py": "sub/y.py",
                "sub/deeper/z.xml": "sub/deeper/z.xml",
                "coverage.xml": "coverage.xml",
            }
        ],
        indirect=True,
        ids=["a tree"],
    )
    def test_listing(
        self,
        fs,
        root,
    ):
        assert sorted(fs.files_under(root)) == ["coverage.xml", "sub/deeper/z.xml", "sub/y.py", "x.py"]
        assert fs.files_under(fs.join(root, "nope")) == []
        assert fs.glob(root, "*.py") == [fs.join(root, "x.py")]
        assert fs.glob(root, "*/*.py") == [fs.join(root, "sub", "y.py")]  # one level, not two

    @pytest.mark.parametrize(
        ("files", "links"),
        [({"target.txt": "t"}, {"link.txt": "target.txt", "dangling.txt": "nothing.txt"})],
        indirect=True,
        ids=["a link and a dangling one"],
    )
    def test_links(
        self,
        fs,
        root,
    ):
        assert fs.is_link(fs.join(root, "link.txt"))
        assert fs.real_path(fs.join(root, "link.txt")) == fs.join(root, "target.txt")
        assert fs.read_text(fs.join(root, "link.txt")) == "t"
        fs.remove(fs.join(root, "link.txt"))  # the link goes, its target stays
        assert (fs.lexists(fs.join(root, "link.txt")), fs.exists(fs.join(root, "target.txt"))) == (False, True)
        assert (fs.lexists(fs.join(root, "dangling.txt")), fs.exists(fs.join(root, "dangling.txt"))) == (True, False)

    @pytest.mark.parametrize(
        ("files", "links"), [({"t/x.txt": "x"}, {"t/l.txt": "t/x.txt"})], indirect=True, ids=["a folder with a link"]
    )
    def test_copying_and_removing_folders(
        self,
        fs,
        root,
    ):
        fs.copy_tree(fs.join(root, "t"), fs.join(root, "copy"))
        assert fs.is_link(fs.join(root, "copy", "l.txt"))  # links stay links
        assert fs.read_text(fs.join(root, "copy", "x.txt")) == "x"
        fs.remove_tree(fs.join(root, "copy"))
        assert not fs.lexists(fs.join(root, "copy"))
