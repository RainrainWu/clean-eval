"""The knowledge graph as the business rules see it: relationships between files, functions, tests and flows."""
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Graph:
    nodes: dict    # qualified name -> (kind, repo-relative file, is_test), Python only
    links: dict    # file -> files it imports or calls into (resolved edges only)
    tested: dict   # function qualified name -> test files linked to it
    flows: list    # (criticality, entry file, files on the flow)
    stats: dict
    has_flows: bool = True  # False when the graph source cannot see execution flows

    def dependents(self, file_mod):
        """(fan_in, fan_out): for each module, the modules that depend on it and the modules it depends on."""
        fan_in, fan_out = defaultdict(set), defaultdict(set)
        for sf, targets in self.links.items():
            for tf in targets:
                ms, mt = file_mod.get(sf), file_mod.get(tf)
                if ms and mt and ms != mt:
                    fan_out[ms].add(mt)
                    fan_in[mt].add(ms)
        return fan_in, fan_out

    def functions_by_module(self, file_mod):
        out = defaultdict(list)
        for qn, (kind, fp, is_test) in self.nodes.items():
            if kind == "Function" and not is_test and fp in file_mod:
                out[file_mod[fp]].append(qn)
        return out

    def test_files_to_modules(self, file_mod):
        out = defaultdict(set)
        for qn, test_files in self.tested.items():
            kind, fp, _ = self.nodes[qn]
            if kind == "Function" and fp in file_mod:
                for tf in test_files:
                    out[tf].add(file_mod[fp])
        return out
