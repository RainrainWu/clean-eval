"""Queries over the knowledge graph (use_cases/rules/graph.py): dependents, functions and test files by module."""
import unittest

from clean_eval.domain.graph import Graph
from clean_eval.use_cases.rules.graph import dependents, functions_by_module, test_files_to_modules


class GraphQueries(unittest.TestCase):
    def test_dependents_functions_and_test_files_by_module(self):
        nodes = {"a.py::f": ("Function", "a.py", False), "b.py::g": ("Function", "b.py", False), "c.py::t": ("Function", "c.py", True)}
        g = Graph(nodes=nodes, links={"a.py": {"b.py"}}, tested={"a.py::f": {"tests/test_a.py"}}, flows=[], stats={})
        file_mod = {"a.py": "A", "b.py": "B", "c.py": "C"}
        fan_in, fan_out = dependents(g, file_mod)
        self.assertEqual((fan_in["B"], fan_out["A"]), ({"A"}, {"B"}))
        self.assertEqual(sorted(functions_by_module(g, file_mod)), ["A", "B"])
        self.assertEqual(test_files_to_modules(g, file_mod), {"tests/test_a.py": {"A"}})


if __name__ == "__main__":
    unittest.main()
