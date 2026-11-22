"""The code-review-graph gateway (external/code_review_graph.py): the CLI's build is mocked (the `graph_build` fixture,
subprocess.run), and the graph.db a build leaves is made for real with sqlite3 (the `graph_db` fixture), so the gateway
reads a real database."""

from subprocess import CalledProcessError

import pytest

from clean_eval.domain.errors import HeatmapError
from clean_eval.external.code_review_graph import CodeReviewGraphGateway

CLI = "/opt/code-review-graph/bin/code-review-graph"  # where the composition root found it
# The graph of repository /r: f in src/a.py calls g in src/b.py and print (unresolved), and is tested by
# tests/test_a.py; two flows start at f and g; and a node in a Terraform file.
GRAPH_DB = """
    create table nodes (id integer primary key, kind text, qualified_name text, file_path text, language text, is_test integer);
    create table edges (kind text, source_qualified text, target_qualified text, confidence_tier text, target_resolution text);
    create table flows (id integer primary key, criticality real, entry_point_id integer);
    create table flow_memberships (flow_id integer, node_id integer);
    insert into nodes values (1, 'Function', '/r/src/a.py::f', '/r/src/a.py', 'python', 0), (2, 'Function', '/r/src/b.py::g', '/r/src/b.py', 'python', 0),
        (3, 'Test', '/r/tests/test_a.py::test_f', '/r/tests/test_a.py', 'python', 1), (4, 'Function', '/r/x.tf::r', '/r/x.tf', 'hcl', 0);
    insert into edges values ('CALLS', '/r/src/a.py::f', '/r/src/b.py::g', 'EXTRACTED', 'direct'),
        ('TESTED_BY', '/r/src/a.py::f', '/r/tests/test_a.py::test_f', 'EXTRACTED', null),
        ('CALLS', '/r/src/a.py::f', 'print', 'UNRESOLVED', 'unresolved');
    insert into flows values (1, 0.9, 1), (2, null, 2);
    insert into flow_memberships values (1, 1), (1, 2);
"""


class TestGraphOf:
    """graph_of builds the knowledge graph of a working tree into a data folder with the CLI, then reads the graph.db
    it leaves: Python only, with paths relative to the repository. A build that fails, or leaves no graph to read, is a
    HeatmapError."""

    @pytest.mark.parametrize("graph_db", [GRAPH_DB], indirect=True)
    def test_builds_then_reads_links_tests_and_flows_with_repo_relative_paths(
        self,
        graph_build,
        graph_db,
        graph_dir,
    ):
        graph = CodeReviewGraphGateway(CLI).graph_of("/r", graph_dir)
        graph_build.assert_called_once_with(
            [CLI, "build", "--repo", "/r", "--data-dir", graph_dir, "-q"], capture_output=True, text=True, check=True
        )
        assert graph.links == {"src/a.py": {"src/b.py"}}
        assert graph.tested == {"/r/src/a.py::f": {"tests/test_a.py"}}
        assert graph.flows == [(0.9, "src/a.py", {"src/a.py", "src/b.py"}), (0.0, "src/b.py", set())]
        assert (graph.has_flows, graph.stats["nodes"], graph.stats["source"]) == (True, 4, "code-review-graph")
        assert "/r/x.tf::r" not in graph.nodes  # Python only

    @pytest.mark.parametrize(
        ("graph_db", "expected"),
        [(None, "no readable graph.db"), ("create table other (a integer);", "cannot read")],
        indirect=["graph_db"],
        ids=["no database", "not a graph"],
    )
    def test_a_build_without_a_readable_database_fails(
        self,
        graph_build,
        graph_db,
        graph_dir,
        expected,
    ):
        with pytest.raises(HeatmapError) as raised:
            CodeReviewGraphGateway(CLI).graph_of("/r", graph_dir)
        assert expected in str(raised.value)

    @pytest.mark.parametrize(
        ("graph_build", "expected"),
        [
            ({"side_effect": CalledProcessError(1, [CLI, "build"], stderr="boom")}, "boom"),
            ({"side_effect": FileNotFoundError(2, "No such file or directory", CLI)}, "install it"),
        ],
        indirect=["graph_build"],
        ids=["a failing build", "no CLI"],
    )
    def test_a_failing_or_missing_cli_fails(
        self,
        graph_build,
        graph_dir,
        expected,
    ):
        with pytest.raises(HeatmapError) as raised:
            CodeReviewGraphGateway(CLI).graph_of("/r", graph_dir)
        assert expected in str(raised.value)
