"""Queries over the knowledge graph (use_cases/rules/graph.py): dependents, functions and test files by module."""

from clean_eval.domain.graph import Graph
from clean_eval.use_cases.rules import graph  # a module, so pytest doesn't collect graph.test_files_to_modules


def test_dependents_functions_and_test_files_by_module():
    nodes = {
        "a.py::f": ("Function", "a.py", False),
        "b.py::g": ("Function", "b.py", False),
        "c.py::t": ("Function", "c.py", True),
    }
    g = Graph(nodes=nodes, links={"a.py": {"b.py"}}, tested={"a.py::f": {"tests/test_a.py"}}, flows=[], stats={})
    file_mod = {"a.py": "A", "b.py": "B", "c.py": "C"}
    fan_in, fan_out = graph.dependents(g, file_mod)
    assert (fan_in["B"], fan_out["A"]) == ({"A"}, {"B"})
    assert sorted(graph.functions_by_module(g, file_mod)) == ["A", "B"]
    assert graph.test_files_to_modules(g, file_mod) == {"tests/test_a.py": {"A"}}
