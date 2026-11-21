"""What the knowledge graph says about modules: who depends on whom, their functions, and the tests linked to them.
`file_mod` maps each source file to its module."""
from collections import defaultdict


def dependents(graph, file_mod):
    """(fan_in, fan_out): for each module, the modules that depend on it and the modules it depends on."""
    fan_in, fan_out = defaultdict(set), defaultdict(set)
    for sf, targets in graph.links.items():
        for tf in targets:
            ms, mt = file_mod.get(sf), file_mod.get(tf)
            if ms and mt and ms != mt:
                fan_out[ms].add(mt)
                fan_in[mt].add(ms)
    return fan_in, fan_out


def functions_by_module(graph, file_mod):
    out = defaultdict(list)
    for qn, (kind, fp, is_test) in graph.nodes.items():
        if kind == "Function" and not is_test and fp in file_mod:
            out[file_mod[fp]].append(qn)
    return out


def test_files_to_modules(graph, file_mod):
    out = defaultdict(set)
    for qn, test_files in graph.tested.items():
        kind, fp, _ = graph.nodes[qn]
        if kind == "Function" and fp in file_mod:
            for tf in test_files:
                out[tf].add(file_mod[fp])
    return out
