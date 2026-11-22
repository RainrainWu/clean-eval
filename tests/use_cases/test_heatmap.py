"""The heat map's use cases (use_cases/heatmap.py), through fakes of their interfaces: the sources a rating
reads, coverage, rating a repository, summarizing, and refreshing the README."""

import pytest

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Graph, Judgement, RatingCache, Snapshot
from clean_eval.use_cases.heatmap import (
    GENERATED,
    MAX_FILE_BYTES,
    content_id,
    count_loc,
    coverage_line_rates,
    finalize_heatmap,
    rate,
    rate_revision,
    rate_working_tree,
    read_revision,
    read_working_tree,
    summarize_heatmap,
    update_readme,
)
from clean_eval.use_cases.interfaces import Clock, DiagramPresenter, KnowledgeGraph, ReportPresenter
from clean_eval.use_cases.rules.heatmap import MARKERS
from tests.domain.builders import heatmap, rating, sf
from tests.use_cases.fakes import MemoryFileSystem, MemoryGit, blob_id, fake

# The sources a rating reads (use_cases/heatmap.py): the tracked Python files of a working tree as they are on disk,
# through the Git and FileSystem interfaces, or of a revision, from git objects.

P = Policy()


class TestSources:
    """A history and a working tree that differs from it: what a rating reads of each."""

    @pytest.fixture
    def git(self, git):
        """Two commits; the second adds a generated file, a huge one, and one the working tree has lost. A symlink is staged."""
        git.commit("init", {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"})
        git.commit(
            "change",
            {
                "src/a.py": "def f():\n    return 2\n",
                "src/x_pb2.py": "X = 1\n",
                "src/gone.py": "G = 1\n",
                "src/huge.py": "#\n" * (MAX_FILE_BYTES // 2 + 1),
                "docs/n.md": "n\n",
            },
        )
        git.stage("src/link.py")
        return git

    @pytest.fixture
    def fs(self, fs, git):
        """The working tree: the last commit without src/gone.py, src/a.py edited, an untracked file and the symlink."""
        for path, text in git.commits[-1]["tree"].items():
            if path != "src/gone.py":
                fs.write_text("/repo/" + path, text)
        fs.write_text("/repo/src/a.py", "def f():\n    return 3\n")  # edited, not committed
        fs.write_text("/repo/src/untracked.py", "U = 1\n")
        fs.link("/repo/src/link.py", "/etc/hosts")
        return fs

    def test_a_working_tree_is_its_tracked_python_files_as_they_are_on_disk(self, git, fs):
        snap, read = read_working_tree(git, fs, P)
        files = {f.path: f for f in read}
        assert sorted(files) == ["src/a.py", "tests/test_a.py"]  # not generated, linked, gone, huge or untracked
        assert files["src/a.py"].text == "def f():\n    return 3\n"
        assert files["src/a.py"].blob == blob_id("def f():\n    return 3\n")  # git's id: caches are shared
        assert files["tests/test_a.py"].is_test
        assert (snap.repo, snap.ref, snap.sha, snap.root) == ("demo", "main", git.rev_parse("HEAD", True), "/repo")

    def test_a_revision_is_read_from_git_objects_sharing_their_contents(self, git):
        old_sha = git.commits[0]["sha"]
        blobs = {}
        snap, old = read_revision(git, old_sha, P, blobs)
        assert {f.path: f.text for f in old} == {
            "src/a.py": "def f():\n    return 1\n",
            "tests/test_a.py": "from src.a import f\n",
        }
        assert (snap.ref, snap.sha, len(blobs)) == (old_sha, old_sha[:7], 2)
        _, head = read_revision(git, "HEAD", P, blobs)
        assert sorted(f.path for f in head) == ["src/a.py", "src/gone.py", "tests/test_a.py"]  # not generated or huge
        assert len(blobs) == 4  # the unchanged test file is read once


def test_helpers():
    assert count_loc("a = 1\n\n# note\n  \nb = 2\n") == 2
    assert all(GENERATED.search("/" + p) for p in ("x_pb2.py", "a/generated/b.py", "vendor/x.py"))
    assert content_id("hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"  # `git hash-object` of "hi\n"


# Line coverage from a working tree's Cobertura coverage.xml (use_cases/heatmap.py), through the FileSystem interface.

REPORT = '<coverage><class name="a" filename="src/a.py" line-rate="0.75"/></coverage>'


def test_the_report_at_or_one_level_below_the_root():
    fs = MemoryFileSystem({"/repo/deep/er/coverage.xml": REPORT})
    assert coverage_line_rates(fs, "/repo") == {}  # two levels down is not looked at
    fs.write_text("/repo/build/coverage.xml", REPORT)
    assert coverage_line_rates(fs, "/repo") == {"src/a.py": 0.75}


def test_a_symlinked_report_is_not_read():
    fs = MemoryFileSystem({"/elsewhere.xml": REPORT})
    fs.link("/repo/coverage.xml", "/elsewhere.xml")
    assert coverage_line_rates(fs, "/repo") == {}


# Rating a repository (use_cases/heatmap.py): the pure rating of a snapshot's files, and rating a working tree
# or a revision through fakes of the Git, FileSystem, Clock and KnowledgeGraph interfaces.

TODAY = "2026-11-16"
SNAP = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"
CLOCK = fake(Clock, today=lambda: TODAY)


def graph(has_flows=True):
    nodes = {
        "src/api/a.py::f": ("Function", "src/api/a.py", False),
        "src/core/c.py::g": ("Function", "src/core/c.py", False),
    }
    return Graph(
        nodes=nodes,
        links={"src/api/a.py": {"src/core/c.py"}},
        tested={"src/api/a.py::f": {"tests/unit/test_a.py"}},
        flows=[(0.9, "src/api/a.py", {"src/api/a.py", "src/core/c.py"})] if has_flows else [],
        stats={"source": "fake", "nodes": 2, "edges": 1, "flows": 1 if has_flows else 0},
        has_flows=has_flows,
    )


def files():
    return [
        sf("src/api/a.py", 300, "def f(x: int) -> int:\n    return x\n"),
        sf("src/api/b.py", 300),
        sf("src/core/c.py", 100, CORE),
        sf("tests/unit/test_a.py", 50, test=True),
    ]


# The rating itself: a pure function of a snapshot's files and their knowledge graph.


def test_rates_every_module():
    h = rate(SNAP, files(), graph(), P, TODAY, depth=2)
    assert (h.repo, h.ref, h.sha, h.generated_at, h.policy_version) == ("demo", "main", "abc1234", TODAY, 2)
    assert [m.name for m in h.modules] == ["src/api", "src/core"]  # largest first; tests are not modules
    api, core = h.modules
    assert api.facts is not None
    assert core.facts is not None
    assert (api.loc, api.tests.functions, api.tests.tested, api.tests.scales) == (600, 1, 1, {"unit": 1})
    assert api.baseline.model_dump() == api.scores.model_dump(exclude={"overall"})
    assert (api.facts.funcs, api.facts.typed_share) == ([(2, 1)], 1.0)  # read from the source text
    assert core.facts.effect_counts == {"db_write": 2}
    assert core.facts.effect_examples == {"db_write": "src/core/c.py:2"}
    assert core.facts.fan_in == 1
    assert h.notes[0].startswith("Knowledge graph (fake): 2 nodes")
    assert h.loc_total == 700


def test_without_flows_the_facts_say_so():
    facts = rate(SNAP, files(), graph(has_flows=False), P, TODAY, depth=2).modules[0].facts
    assert facts is not None
    assert not facts.flows_available


def test_excluded_code_is_not_rated():
    names = [
        m.name
        for m in rate(
            SNAP, files(), graph(), Policy.from_overrides({"exclude": ["src/core/*"]}), TODAY, depth=2
        ).modules
    ]
    assert names
    assert not any("core" in n for n in names), names


@pytest.mark.parametrize("source", [[], [sf("tests/unit/test_a.py", 50, test=True)]], ids=["no files", "tests only"])
def test_no_python_source_to_rate(source):
    with pytest.raises(NoPythonSource):
        rate(SNAP, source, graph(), P, TODAY)


class TestRateRepository:
    """A working tree is read through Git and the FileSystem, a revision from git objects alone."""

    @pytest.fixture
    def git(self, git):
        """One commit: a module that writes to a database, and its test."""
        git.commit("init", {"src/core/c.py": CORE, "tests/test_c.py": "from src.core.c import g\n"})
        return git

    @pytest.fixture
    def fs(self, fs):
        """The working tree of that commit."""
        fs.write_text("/repo/src/core/c.py", CORE)
        fs.write_text("/repo/tests/test_c.py", "from src.core.c import g\n")
        return fs

    def test_a_working_tree_with_the_builtin_graph_and_coverage(self, git, fs):
        fs.write_text(
            "/repo/coverage.xml", '<coverage><class name="c" filename="src/core/c.py" line-rate="0.5"/></coverage>'
        )
        h = rate_working_tree(git, fs, CLOCK, P)
        assert (h.generated_at, [m.name for m in h.modules]) == (TODAY, ["src"])
        assert h.modules[0].facts is not None
        assert (h.modules[0].facts.line_rate, h.modules[0].tests.tested) == (0.5, 1)
        assert h.notes[0].startswith("Knowledge graph (built-in ast)")

    def test_a_working_tree_with_a_knowledge_graph(self, git, fs):
        built = []
        knowledge_graph = fake(
            KnowledgeGraph,
            graph_of=lambda root, data_dir: built.append((root, data_dir, fs.is_dir(data_dir))) or graph(),
        )
        h = rate_working_tree(git, fs, CLOCK, P, knowledge_graph)
        assert built == [("/repo", "/repo/.git/clean-eval-graph", True)]  # under .git by default, made for it
        assert h.notes[0].startswith("Knowledge graph (fake)")
        assert h.modules[0].facts is not None
        assert h.modules[0].facts.flows_available

    def test_revisions_share_their_work_through_the_cache(self, git):
        first = git.rev_parse("HEAD")
        second = git.commit("more", {"src/core/d.py": "def h():\n    pass\n"})
        cache = RatingCache()
        old = rate_revision(git, CLOCK, first, P, cache)
        assert (old.ref, old.sha, old.generated_at, old.modules[0].files) == (first, first[:7], TODAY, 1)
        assert len(cache.blobs) == 2
        rate_revision(git, CLOCK, second, P, cache)
        assert (len(cache.blobs), len(cache.syntax)) == (3, 3)  # the unchanged files are read and parsed once

    def test_a_repository_without_python_source(self):
        git = MemoryGit()
        git.commit("docs", {"README.md": "# r\n"})
        with pytest.raises(NoPythonSource):
            rate_working_tree(git, MemoryFileSystem({"/repo/README.md": "# r\n"}), CLOCK, P)
        with pytest.raises(NoPythonSource):
            rate_revision(git, CLOCK, "HEAD", P, RatingCache())


# Summarizing a heat map (use_cases/heatmap.py): levels applied, then a
# ReportPresenter's report.


def test_levels_are_applied_before_summarizing():
    report = fake(ReportPresenter, report=lambda heatmap: "%d judged" % heatmap.judged_modules)
    judged = Judgement.model_validate({"modules": {"m": {"business": {"level": 0, "reason": "r"}}}})
    final, text = summarize_heatmap(report, heatmap(rating()), judged, P)
    assert (text, final.judged_modules) == ("1 judged", 1)
    assert final.modules[0].scores.biz >= 75
    assert finalize_heatmap(heatmap(rating()), None, P).judged_modules == 0


# Refreshing the README's heat-map section and its diagram (use_cases/heatmap.py), through the FileSystem
# interface and a DiagramPresenter.

README, SVG = "/repo/README.md", "/repo/docs/risk-heatmap.svg"
DIAGRAM = fake(
    DiagramPresenter,
    svg=lambda heatmap: "<svg/>",
    readme_block=lambda heatmap, link, fp: (
        "%s\n![map](%s)\n<!-- risk-heat-map:fingerprint=%s -->\n%s\n" % (MARKERS[0], link, fp, MARKERS[1])
    ),
)


class TestUpdateReadme:
    """Refreshing the README section and its diagram."""

    @pytest.fixture
    def fs(self, fs):
        """The repository's README, before any heat map."""
        fs.write_text(README, "# Demo\n")
        return fs

    def test_writes_when_ratings_change_and_not_otherwise(self, fs):
        first = update(fs, heatmap(rating()))
        assert (first.changed, first.action) == (True, "ADD")
        assert (
            "![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint
            in fs.read_text(README)
        )
        assert fs.read_text(SVG) == "<svg/>"
        assert not update(fs, heatmap(rating())).changed  # the same ratings: nothing to write
        fs.remove(SVG)  # diagram deleted: rewritten though the ratings match
        assert update(fs, heatmap(rating())).changed
        moved = update(fs, heatmap(rating(biz=90)))
        assert (moved.changed, moved.action) == (True, "UPDATE")

    def test_the_image_is_linked_relative_to_the_readme(self, fs):
        update(fs, heatmap(rating()), "/repo/docs/README.md")
        text = fs.read_text("/repo/docs/README.md")
        assert "![map](risk-heatmap.svg)" in text
        assert text.endswith("\n")


def update(fs, h, readme=README):
    return update_readme(fs, DIAGRAM, readme, SVG, h, None, P)
