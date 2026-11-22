"""The code-review-graph tool (optional): its CLI builds the knowledge graph of a working tree into a data folder, as an
SQLite database (graph.db) this gateway reads read-only and translates into the domain's Graph. Unlike the built-in
graph it resolves calls and sees execution flows with a criticality score. Working trees only: it parses files on disk.
Everything about code-review-graph (the command, its database schema, SQLite) stays in this module."""

import sqlite3
import subprocess

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.rating import Graph
from clean_eval.use_cases.interfaces import KnowledgeGraph

NODES = "select qualified_name, kind, file_path, is_test from nodes where language = 'python'"
EDGES = (
    "select kind, source_qualified, target_qualified, confidence_tier, target_resolution from edges "
    "where kind in ('IMPORTS_FROM','CALLS','REFERENCES','INHERITS','TESTED_BY')"
)
FLOWS = "select f.id, f.criticality, n.qualified_name from flows f join nodes n on n.id = f.entry_point_id"
FLOW_FILES = "select n.file_path from flow_memberships m join nodes n on n.id = m.node_id where m.flow_id = ?"


def read_graph(
    con: sqlite3.Connection,
    root: str,
) -> Graph:
    """The graph in an open code-review-graph database, Python only, with paths relative to `root`."""
    prefix = root.rstrip("/") + "/"

    def relative(
        path: str,
    ) -> str:
        return path[len(prefix) :] if path.startswith(prefix) else path

    def file_of(
        qualified_name: str,
    ) -> str:
        return relative(qualified_name.split("::")[0])

    def rows(
        query: str,
        params: tuple = (),
    ) -> list:
        return con.execute(query, params).fetchall()

    nodes = {q: (kind, relative(fp), bool(t)) for q, kind, fp, t in rows(NODES)}
    links, tested = {}, {}
    for kind, s, t, tier, res in rows(EDGES):
        if tier == "UNRESOLVED" or res == "unresolved":
            continue
        if kind == "TESTED_BY":
            if s in nodes:
                tested.setdefault(s, set()).add(file_of(t))
        elif file_of(s) != file_of(t):
            links.setdefault(file_of(s), set()).add(file_of(t))
    flows = []
    for fid, crit, entry in rows(FLOWS):
        files = {relative(r[0]) for r in rows(FLOW_FILES, (fid,))}
        flows.append((crit or 0.0, file_of(entry), files))  # a flow without a criticality weighs nothing
    stats = {
        "source": "code-review-graph",
        "nodes": rows("select count(*) from nodes")[0][0],
        "edges": rows("select count(*) from edges")[0][0],
        "flows": len(flows),
    }
    return Graph(nodes=nodes, links=links, tested=tested, flows=flows, stats=stats, has_flows=True)


class CodeReviewGraphGateway(KnowledgeGraph):
    def __init__(
        self,
        executable: str,
    ) -> None:
        self.executable = executable  # resolved by the composition root (PATH, else ~/.local/bin)

    def graph_of(
        self,
        repo_root: str,
        data_dir: str,
    ) -> Graph:
        try:
            subprocess.run(
                [self.executable, "build", "--repo", repo_root, "--data-dir", data_dir, "-q"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            raise HeatmapError(
                "code-review-graph build failed (install it, or use --graph builtin): %s"
                % (getattr(e, "stderr", None) or e)
            )
        db = data_dir.rstrip("/") + "/graph.db"
        try:
            con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        except sqlite3.Error as e:
            raise HeatmapError("code-review-graph produced no readable graph.db in %s: %s" % (data_dir, e))
        try:
            return read_graph(con, repo_root)
        except sqlite3.Error as e:
            raise HeatmapError("cannot read the knowledge graph %s: %s" % (db, e))
        finally:
            con.close()
