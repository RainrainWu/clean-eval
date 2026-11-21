"""GraphProvider backed by code-review-graph (optional): its CLI builds the graph of a working tree into a separate data
folder, and the SQLite database it writes is read read-only. Unlike the built-in graph it resolves calls and sees
execution flows with a criticality score. Working trees only: it parses files on disk. The translation from rows to the
Graph model lives here; the CLI, the database and the folder are reached through their gateways."""
from collections import defaultdict

from ..domain.errors import HeatmapError
from ..domain.graph import Graph

NODES = "select qualified_name, kind, file_path, is_test from nodes where language = 'python'"
EDGES = ("select kind, source_qualified, target_qualified, confidence_tier, target_resolution from edges "
         "where kind in ('IMPORTS_FROM','CALLS','REFERENCES','INHERITS','TESTED_BY')")
FLOWS = "select f.id, f.criticality, n.qualified_name from flows f join nodes n on n.id = f.entry_point_id"
FLOW_FILES = "select n.file_path from flow_memberships m join nodes n on n.id = m.node_id where m.flow_id = ?"


def read_graph_db(db, root):
    """The graph in a code-review-graph database (an open Database), Python only, with repo-relative paths."""
    prefix = root.rstrip("/") + "/"
    rel = lambda p: p[len(prefix):] if p.startswith(prefix) else p  # noqa: E731
    fof = lambda q: rel(q.split("::")[0])  # noqa: E731
    nodes = {q: (kind, rel(fp), bool(t)) for q, kind, fp, t in db.rows(NODES)}
    links, tested = defaultdict(set), defaultdict(set)
    for kind, s, t, tier, res in db.rows(EDGES):
        if tier == "UNRESOLVED" or res == "unresolved":
            continue
        if kind == "TESTED_BY":
            if s in nodes:
                tested[s].add(fof(t))
        elif fof(s) != fof(t):
            links[fof(s)].add(fof(t))
    flows = []
    for fid, crit, entry in db.rows(FLOWS):
        files = {rel(r[0]) for r in db.rows(FLOW_FILES, (fid,))}
        flows.append((crit or 0.0, fof(entry), files))  # a flow without a criticality weighs nothing
    stats = {"source": "code-review-graph", "nodes": db.rows("select count(*) from nodes")[0][0],
             "edges": db.rows("select count(*) from edges")[0][0], "flows": len(flows)}
    return Graph(nodes=nodes, links=dict(links), tested=dict(tested), flows=flows, stats=stats, has_flows=True)


class CodeReviewGraph:
    def __init__(self, fs, builder, open_db, data_dir):
        self.fs, self.builder, self.open_db, self.data_dir = fs, builder, open_db, data_dir

    def graph_for(self, snapshot, files):
        self.fs.make_dirs(self.data_dir)
        self.builder.build(snapshot.root, self.data_dir)
        db_path = self.fs.join(self.data_dir, "graph.db")
        if not self.fs.exists(db_path):
            raise HeatmapError("graph build produced no graph.db in %s" % self.data_dir)
        with self.open_db(db_path) as db:
            return read_graph_db(db, snapshot.root)
