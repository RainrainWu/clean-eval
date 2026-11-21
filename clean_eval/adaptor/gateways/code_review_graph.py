"""GraphProvider backed by the code-review-graph CLI (optional): builds the graph of a working tree into a separate data
folder, then reads its SQLite database read-only. Unlike the built-in graph it resolves calls and sees execution flows
with a criticality score. Working trees only: it parses files on disk."""
import shutil
import sqlite3
import subprocess
from collections import defaultdict
from pathlib import Path

from ...domain.errors import HeatmapError
from ...domain.graph import Graph


def read_graph_db(db_path, root):
    """The graph in a code-review-graph database, Python only, with repo-relative paths."""
    prefix = root.rstrip("/") + "/"
    rel = lambda p: p[len(prefix):] if p.startswith(prefix) else p  # noqa: E731
    fof = lambda q: rel(q.split("::")[0])  # noqa: E731
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
        nodes = {q: (kind, rel(fp), bool(t)) for q, kind, fp, t in
                 con.execute("select qualified_name, kind, file_path, is_test from nodes where language = 'python'")}
        links, tested = defaultdict(set), defaultdict(set)
        query = ("select kind, source_qualified, target_qualified, confidence_tier, target_resolution from edges "
                 "where kind in ('IMPORTS_FROM','CALLS','REFERENCES','INHERITS','TESTED_BY')")
        for kind, s, t, tier, res in con.execute(query):
            if tier == "UNRESOLVED" or res == "unresolved":
                continue
            if kind == "TESTED_BY":
                if s in nodes:
                    tested[s].add(fof(t))
            elif fof(s) != fof(t):
                links[fof(s)].add(fof(t))
        flows = []
        for fid, crit, entry in con.execute("select f.id, f.criticality, n.qualified_name from flows f "
                                            "join nodes n on n.id = f.entry_point_id").fetchall():
            files = {rel(r[0]) for r in con.execute("select n.file_path from flow_memberships m join nodes n on n.id = m.node_id "
                                                    "where m.flow_id = ?", (fid,))}
            flows.append((crit, fof(entry), files))
        stats = {"source": "code-review-graph", "nodes": con.execute("select count(*) from nodes").fetchone()[0],
                 "edges": con.execute("select count(*) from edges").fetchone()[0], "flows": len(flows)}
        con.close()
    except sqlite3.Error as e:
        raise HeatmapError("cannot read the knowledge graph %s: %s" % (db_path, e))
    return Graph(nodes, dict(links), dict(tested), flows, stats, has_flows=True)


class CodeReviewGraph:
    def __init__(self, data_dir, cli=None):
        self.data_dir, self.cli = Path(data_dir), cli

    def graph_for(self, snapshot, files):
        cli = self.cli or shutil.which("code-review-graph") or str(Path.home() / ".local" / "bin" / "code-review-graph")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([cli, "build", "--repo", snapshot.root, "--data-dir", str(self.data_dir), "-q"],
                           capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise HeatmapError("code-review-graph build failed (install it, or use --graph builtin): %s"
                               % (getattr(e, "stderr", None) or e))
        db = self.data_dir / "graph.db"
        if not db.exists():
            raise HeatmapError("graph build produced no graph.db in %s" % self.data_dir)
        return read_graph_db(str(db), snapshot.root)
