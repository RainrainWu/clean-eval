"""The heat map's use cases (use_cases/heatmap.py), with their dependencies mocked: the sources a rating reads,
coverage, rating a snapshot, a working tree or a revision, summarizing, and refreshing the README."""

from typing import Any
from unittest.mock import MagicMock, call

import pytest

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.rating import Graph, Snapshot
from clean_eval.domain.review import RunFolders
from clean_eval.use_cases.heatmap import (
    GENERATED,
    MAX_FILE_BYTES,
    agent_judgement,
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
from clean_eval.use_cases.rules.heatmap import MARKERS
from tests.use_cases.fakes import MemoryGit

# The sources a rating reads: a history of two commits, the second adding a generated file, a huge one and one the
# working tree has lost.
INIT = {"src/a.py": "def f():\n    return 1\n", "tests/test_a.py": "from src.a import f\n"}
CHANGE = {
    "src/a.py": "def f():\n    return 2\n",
    "src/x_pb2.py": "X = 1\n",
    "src/gone.py": "G = 1\n",
    "src/huge.py": "#\n" * (MAX_FILE_BYTES // 2 + 1),
    "docs/n.md": "n\n",
}
SOURCES_HISTORY = [{"subject": "init", "files": INIT}, {"subject": "change", "files": CHANGE}]
# What git tracks: the last commit's files, and a symlink that is staged.
TRACKED = sorted({*INIT, *CHANGE, "src/link.py"})
# The working tree: the last commit without src/gone.py, src/a.py edited, an untracked file, and the symlink.
EDITED = "def f():\n    return 3\n"
WORKING_TREE = {
    "/repo/" + path: text
    for path, text in {**INIT, **CHANGE, "src/a.py": EDITED, "src/untracked.py": "U = 1\n"}.items()
    if path != "src/gone.py"
}
SYMLINK = {"/repo/src/link.py": "/etc/hosts"}

# A Cobertura coverage report.
REPORT = '<coverage><class name="a" filename="src/a.py" line-rate="0.75"/></coverage>'

# Rating a snapshot: its files are two modules, src/api (600 lines) and src/core (100), and a test. In their knowledge
# graph src/api calls into src/core, its function is tested, and a flow runs through both.
SNAPSHOT = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")
CORE = "def g(con):\n    con.execute('INSERT INTO t VALUES (1)')\n    con.commit()\n"
TEST_FILE = {"path": "tests/unit/test_a.py", "loc": 50, "is_test": True}
SOURCES = [
    {"path": "src/api/a.py", "loc": 300, "text": "def f(x: int) -> int:\n    return x\n"},
    {"path": "src/api/b.py", "loc": 300},
    {"path": "src/core/c.py", "loc": 100, "text": CORE},
    TEST_FILE,
]
GRAPH: dict[str, Any] = {
    "nodes": {
        "src/api/a.py::f": ("Function", "src/api/a.py", False),
        "src/core/c.py::g": ("Function", "src/core/c.py", False),
    },
    "links": {"src/api/a.py": {"src/core/c.py"}},
    "tested": {"src/api/a.py::f": {"tests/unit/test_a.py"}},
    "flows": [(0.9, "src/api/a.py", {"src/api/a.py", "src/core/c.py"})],
    "stats": {"source": "fake", "nodes": 2, "edges": 1, "flows": 1},
}
GRAPH_WITHOUT_FLOWS = {**GRAPH, "flows": [], "stats": {**GRAPH["stats"], "flows": 0}, "has_flows": False}

# Rating a repository: one commit of a module that writes to a database and its test, and its working tree.
TEST_C = "from src.core.c import g\n"
REPOSITORY = [{"subject": "init", "files": {"src/core/c.py": CORE, "tests/test_c.py": TEST_C}}]
CHECKOUT = {"/repo/src/core/c.py": CORE, "/repo/tests/test_c.py": TEST_C}
COVERAGE = '<coverage><class name="c" filename="src/core/c.py" line-rate="0.5"/></coverage>'
GRAPH_DIR = "/repo/.git/clean-eval-graph"  # where a KnowledgeGraph builds by default
# A second commit, which adds one module file.
NEW_FILE = "def h():\n    pass\n"
MORE = {"subject": "more", "files": {"src/core/d.py": NEW_FILE}}
# A repository without Python source, and its working tree.
DOCS_ONLY = [{"subject": "docs", "files": {"README.md": "# r\n"}}]
DOCS_CHECKOUT = {"/repo/README.md": "# r\n"}

# Summarizing: a reviewer's level for module "m", and a ReportPresenter whose report says how many modules were judged
# when it was asked (str.format reads them off the heat map it is given).
LEVELED = {"modules": {"m": {"business": {"level": 0, "reason": "r"}}}}
SUMMARY = {"report.side_effect": "{0.judged_modules} judged".format}

# Refreshing the README. The DiagramPresenter answers with an SVG, and with a README block between the markers that
# holds the image link and the fingerprint it is given (str.format fills them in from the call's arguments).
README, SVG = "/repo/README.md", "/repo/docs/risk-heatmap.svg"
DIAGRAM = {
    "svg.return_value": "<svg/>",
    "readme_block.side_effect": (
        MARKERS[0] + "\n![map]({1})\n<!-- risk-heat-map:fingerprint={2} -->\n" + MARKERS[1] + "\n"
    ).format,
}


class TestSourceHelpers:
    """What a read keeps of a file: its lines of code, whether it is generated, and git's id of its content."""

    def test_lines_of_code_leave_out_blank_and_comment_lines(
        self,
    ):
        assert count_loc("a = 1\n\n# note\n  \nb = 2\n") == 2

    @pytest.mark.parametrize(
        "path", ["x_pb2.py", "a/generated/b.py", "vendor/x.py"], ids=["protobuf", "generated folder", "vendored"]
    )
    def test_generated_code_is_recognized(
        self,
        path,
    ):
        assert GENERATED.search("/" + path)

    def test_the_content_id_is_gits_blob_id(
        self,
    ):
        assert content_id("hi\n") == "45b983be36b73c0788dc9cbcb76cbb80fc7bb057"  # `git hash-object` of "hi\n"


@pytest.mark.parametrize("history", [SOURCES_HISTORY], indirect=True)
class TestSources:
    """A history and a working tree that differs from it: what a rating reads of each."""

    @pytest.mark.parametrize(
        ("git", "files", "links"), [({"ls_files.return_value": TRACKED}, WORKING_TREE, SYMLINK)], indirect=True
    )
    def test_a_working_tree_is_its_tracked_python_files_as_they_are_on_disk(
        self,
        git,
        fs,
        policy,
        shas,
    ):
        snapshot, read = read_working_tree(git, fs, policy)
        by_path = {source.path: source for source in read}
        assert sorted(by_path) == ["src/a.py", "tests/test_a.py"]  # not generated, linked, gone, huge or untracked
        assert by_path["src/a.py"].text == EDITED
        assert by_path["src/a.py"].blob == MemoryGit.blob_id(EDITED)  # git's id: caches are shared
        assert by_path["tests/test_a.py"].is_test
        assert (snapshot.repo, snapshot.ref, snapshot.sha, snapshot.root) == ("demo", "main", shas[-1][:7], "/repo")
        assert fs.read_text.call_args_list == [call("/repo/src/a.py"), call("/repo/tests/test_a.py")]  # nothing else

    def test_a_revision_is_read_from_git_objects_sharing_their_contents(
        self,
        git,
        policy,
        shas,
    ):
        blobs = {}
        snapshot, old = read_revision(git, shas[0], policy, blobs)
        assert {source.path: source.text for source in old} == INIT
        assert (snapshot.ref, snapshot.sha, len(blobs)) == (shas[0], shas[0][:7], 2)
        _, head = read_revision(git, "HEAD", policy, blobs)
        # not generated or huge
        assert sorted(source.path for source in head) == ["src/a.py", "src/gone.py", "tests/test_a.py"]
        # the unchanged test file is read once
        assert len(blobs) == 4
        assert git.read_blobs.call_args_list == [
            call([MemoryGit.blob_id(INIT["src/a.py"]), MemoryGit.blob_id(INIT["tests/test_a.py"])]),
            call([MemoryGit.blob_id(CHANGE["src/a.py"]), MemoryGit.blob_id(CHANGE["src/gone.py"])]),
        ]


class TestCoverageLineRates:
    """Line coverage from a working tree's Cobertura coverage.xml, at its root or one level below."""

    @pytest.mark.parametrize(
        ("files", "report"),
        [
            ({"/repo/coverage.xml": REPORT}, "/repo/coverage.xml"),
            ({"/repo/build/coverage.xml": REPORT, "/repo/deep/er/coverage.xml": REPORT}, "/repo/build/coverage.xml"),
        ],
        indirect=["files"],
        ids=["at the root", "one level below"],
    )
    def test_the_report_at_or_one_level_below_the_root(
        self,
        fs,
        report,
    ):
        assert coverage_line_rates(fs, "/repo") == {"src/a.py": 0.75}
        fs.read_text.assert_called_once_with(report)

    @pytest.mark.parametrize("files", [{"/repo/deep/er/coverage.xml": REPORT}], indirect=True)
    def test_a_report_two_levels_down_is_not_looked_at(
        self,
        fs,
    ):
        assert coverage_line_rates(fs, "/repo") == {}
        fs.read_text.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "links"), [({"/elsewhere.xml": REPORT}, {"/repo/coverage.xml": "/elsewhere.xml"})], indirect=True
    )
    def test_a_symlinked_report_is_not_read(
        self,
        fs,
    ):
        assert coverage_line_rates(fs, "/repo") == {}
        fs.read_text.assert_not_called()


class TestRate:
    """The rating itself: a pure function of a snapshot's files and their knowledge graph."""

    @pytest.mark.parametrize(("source_files", "graph"), [(SOURCES, GRAPH)], indirect=True)
    def test_rates_every_module(
        self,
        source_files,
        graph,
        policy,
        today,
    ):
        rated = rate(SNAPSHOT, source_files, graph, policy, today, None, None, 2)  # no coverage or cache; depth 2
        assert (rated.repo, rated.ref, rated.sha, rated.generated_at) == ("demo", "main", "abc1234", today)
        assert rated.policy_version == 2
        # largest first; tests are not modules
        assert [module.name for module in rated.modules] == ["src/api", "src/core"]
        api, core = rated.modules
        assert api.facts is not None
        assert core.facts is not None
        assert (api.loc, api.tests.functions, api.tests.tested, api.tests.scales) == (600, 1, 1, {"unit": 1})
        assert api.baseline.model_dump() == api.scores.model_dump(exclude={"overall"})
        assert (api.facts.funcs, api.facts.typed_share) == ([(2, 1)], 1.0)  # read from the source text
        assert core.facts.effect_counts == {"db_write": 2}
        assert core.facts.effect_examples == {"db_write": "src/core/c.py:2"}
        assert core.facts.fan_in == 1
        assert rated.notes[0].startswith("Knowledge graph (fake): 2 nodes")
        assert rated.loc_total == 700

    @pytest.mark.parametrize(("source_files", "graph"), [(SOURCES, GRAPH_WITHOUT_FLOWS)], indirect=True)
    def test_without_flows_the_facts_say_so(
        self,
        source_files,
        graph,
        policy,
        today,
    ):
        module_facts = rate(SNAPSHOT, source_files, graph, policy, today, None, None, 2).modules[0].facts
        assert module_facts is not None
        assert not module_facts.flows_available

    @pytest.mark.parametrize(
        ("source_files", "graph", "policy"), [(SOURCES, GRAPH, {"exclude": ["src/core/*"]})], indirect=True
    )
    def test_excluded_code_is_not_rated(
        self,
        source_files,
        graph,
        policy,
        today,
    ):
        names = [module.name for module in rate(SNAPSHOT, source_files, graph, policy, today, None, None, 2).modules]
        assert names
        assert not any("core" in name for name in names), names

    @pytest.mark.parametrize(
        ("source_files", "graph"), [([], GRAPH), ([TEST_FILE], GRAPH)], indirect=True, ids=["no files", "tests only"]
    )
    def test_no_python_source_to_rate(
        self,
        source_files,
        graph,
        policy,
        today,
    ):
        with pytest.raises(NoPythonSource):
            rate(SNAPSHOT, source_files, graph, policy, today)


class TestRateWorkingTree:
    """A working tree is read through Git and the FileSystem, and rated with the built-in graph or a KnowledgeGraph."""

    @pytest.mark.parametrize(
        ("history", "files"), [(REPOSITORY, {**CHECKOUT, "/repo/coverage.xml": COVERAGE})], indirect=True
    )
    def test_with_the_builtin_graph_and_coverage(
        self,
        git,
        fs,
        clock,
        policy,
        today,
    ):
        rated = rate_working_tree(git, fs, clock, policy)
        assert (rated.generated_at, [module.name for module in rated.modules]) == (today, ["src"])
        module_facts = rated.modules[0].facts
        assert module_facts is not None
        assert (module_facts.line_rate, rated.modules[0].tests.tested) == (0.5, 1)
        assert rated.notes[0].startswith("Knowledge graph (built-in ast)")

    @pytest.mark.parametrize(
        ("history", "files", "knowledge_graph"),
        [(REPOSITORY, CHECKOUT, {"graph_of.return_value": Graph.model_validate(GRAPH)})],
        indirect=True,
    )
    def test_with_a_knowledge_graph(
        self,
        git,
        fs,
        clock,
        policy,
        knowledge_graph,
    ):
        calls = MagicMock()  # records the calls of both, in order
        calls.attach_mock(fs.make_dirs, "make_dirs")
        calls.attach_mock(knowledge_graph.graph_of, "graph_of")
        rated = rate_working_tree(git, fs, clock, policy, knowledge_graph)
        # built under .git by default, into a folder made for it
        assert calls.mock_calls == [call.make_dirs(GRAPH_DIR), call.graph_of("/repo", GRAPH_DIR)]
        assert rated.notes[0].startswith("Knowledge graph (fake)")
        module_facts = rated.modules[0].facts
        assert module_facts is not None
        assert module_facts.flows_available

    @pytest.mark.parametrize(("history", "files"), [(DOCS_ONLY, DOCS_CHECKOUT)], indirect=True)
    def test_without_python_source(
        self,
        git,
        fs,
        clock,
        policy,
    ):
        with pytest.raises(NoPythonSource):
            rate_working_tree(git, fs, clock, policy)


class TestRateRevision:
    """A revision is read from git objects alone; a RatingCache shares the work across revisions."""

    @pytest.mark.parametrize("history", [[*REPOSITORY, MORE]], indirect=True)
    def test_revisions_share_their_work_through_the_cache(
        self,
        git,
        clock,
        policy,
        rating_cache,
        shas,
        today,
    ):
        first, second = shas
        old = rate_revision(git, clock, first, policy, rating_cache)
        assert (old.ref, old.sha, old.generated_at, old.modules[0].files) == (first, first[:7], today, 1)
        assert len(rating_cache.blobs) == 2
        rate_revision(git, clock, second, policy, rating_cache)
        # the unchanged files are read and parsed once
        assert (len(rating_cache.blobs), len(rating_cache.syntax)) == (3, 3)
        assert git.read_blobs.call_args_list == [
            call([MemoryGit.blob_id(CORE), MemoryGit.blob_id(TEST_C)]),
            call([MemoryGit.blob_id(NEW_FILE)]),
        ]

    @pytest.mark.parametrize("history", [DOCS_ONLY], indirect=True)
    def test_without_python_source(
        self,
        git,
        clock,
        policy,
        rating_cache,
    ):
        with pytest.raises(NoPythonSource):
            rate_revision(git, clock, "HEAD", policy, rating_cache)


class TestSummarizeHeatmap:
    """Summarizing a heat map: the reviewer's levels are applied first, then the ReportPresenter reports on it."""

    @pytest.mark.parametrize(("report_presenter", "judgement"), [(SUMMARY, LEVELED)], indirect=True)
    def test_levels_are_applied_before_summarizing(
        self,
        report_presenter,
        heatmap,
        judgement,
        policy,
    ):
        final, text = summarize_heatmap(report_presenter, heatmap, judgement, policy)
        assert (text, final.judged_modules) == ("1 judged", 1)
        assert final.modules[0].scores.biz >= 75
        report_presenter.report.assert_called_once_with(final)

    def test_without_a_judgement_no_module_is_judged(
        self,
        heatmap,
        policy,
    ):
        assert finalize_heatmap(heatmap, None, policy).judged_modules == 0


@pytest.mark.parametrize(("files", "diagram_presenter"), [({README: "# Demo\n"}, DIAGRAM)], indirect=True)
class TestUpdateReadme:
    """Refreshing the README's heat-map section and its diagram: written when the ratings change, and not otherwise."""

    def test_the_first_heat_map_is_added(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
    ):
        first = update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy)
        assert (first.changed, first.action) == (True, "ADD")
        assert (
            "![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint
            in memory_fs.read_text(README)
        )
        assert memory_fs.read_text(SVG) == "<svg/>"

    def test_the_same_ratings_write_nothing(
        self,
        fs,
        diagram_presenter,
        heatmap,
        policy,
    ):
        first = update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy)
        again = update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy)
        assert not again.changed
        assert again.fingerprint == first.fingerprint
        assert fs.write_text.call_count == 2  # the diagram and the README, by the first update only

    def test_a_deleted_diagram_is_rewritten_though_the_ratings_match(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
    ):
        update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy)
        memory_fs.remove(SVG)
        assert update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy).changed
        assert memory_fs.read_text(SVG) == "<svg/>"

    def test_moved_ratings_update_the_section(
        self,
        fs,
        diagram_presenter,
        heatmap,
        policy,
    ):
        update_readme(fs, diagram_presenter, README, SVG, heatmap, None, policy)
        moved = heatmap.model_copy(deep=True)
        moved.modules[0].scores.biz = 90
        update = update_readme(fs, diagram_presenter, README, SVG, moved, None, policy)
        assert (update.changed, update.action) == (True, "UPDATE")

    def test_the_image_is_linked_relative_to_the_readme(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
    ):
        update_readme(fs, diagram_presenter, "/repo/docs/README.md", SVG, heatmap, None, policy)
        text = memory_fs.read_text("/repo/docs/README.md")
        assert "![map](risk-heatmap.svg)" in text
        assert text.endswith("\n")


# A CI run's folders, and what the agent may write into its own: levels with a token in them, or no judgement at all.
RUN_FOLDERS = RunFolders(workspace="/repo", out="/out", context="/out/context", agent="/out/agent")
TOKEN = "ghs_" + "t" * 36
LEVELS = '{"modules": {"m": {"business": {"level": 0, "reason": "read %s"}, "nope": {"level": 9}}}}' % TOKEN


class TestAgentJudgement:
    """The levels the agent wrote are read redacted, entry by entry; without a JSON object, the static levels stay."""

    @pytest.mark.parametrize("files", [{"/out/agent/judgement.json": LEVELS}], indirect=True)
    @pytest.mark.parametrize("environ", [{"GH_TOKEN": TOKEN}], indirect=True)
    def test_the_levels_are_read_redacted(
        self,
        fs,
        memory_fs,
        env,
        run_log,
    ):
        judgement = agent_judgement(fs, env, run_log, RUN_FOLDERS)
        assert judgement is not None
        assert list(judgement.modules["m"]) == ["business"]  # the invalid entry is dropped
        assert judgement.modules["m"]["business"].reason == "read [REDACTED]"
        assert TOKEN not in memory_fs.read_text("/out/agent/judgement.json")
        run_log.warning.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "warning"),
        [
            ({"/out/agent/.keep": ""}, "the agent wrote no judgement.json"),
            ({"/out/agent/judgement.json": "[1, 2]"}, "not a JSON object"),
            ({"/out/agent/judgement.json": "{not json"}, "not a JSON object"),
        ],
        indirect=["files"],
        ids=["none", "not an object", "not JSON"],
    )
    def test_without_a_judgement_the_static_levels_stay(
        self,
        fs,
        env,
        run_log,
        warning,
    ):
        assert agent_judgement(fs, env, run_log, RUN_FOLDERS) is None
        assert warning in run_log.warning.call_args.args[0]
