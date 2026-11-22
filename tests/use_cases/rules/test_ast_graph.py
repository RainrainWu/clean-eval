"""The built-in knowledge graph (use_cases/rules/ast_graph.py): imports resolved by path, and tests linked to the
functions they import and name."""
import unittest

from tests.domain.builders import sf

from clean_eval.use_cases.rules.ast_graph import builtin_graph


class BuiltinGraphTest(unittest.TestCase):
    FILES = {
        "src/pkg/__init__.py": "from .core import run\n",
        "src/pkg/core.py": "import json\nfrom . import util\n\ndef run():\n    return util.helper()\n\nclass Svc:\n    def go(self):\n        pass\n",
        "src/pkg/util.py": "def helper():\n    return 1\n",
        "tools/json.py": "X = 1\n",
        "lambdas/x/handler.py": "import utils\n\ndef handler(e, c):\n    return utils.f()\n",
        "lambdas/x/utils.py": "def f():\n    pass\n",
        "other/deep/utils.py": "def f():\n    pass\n",
        "tests/test_core.py": "from pkg import run\nfrom pkg.core import Svc\n\ndef test_run():\n    assert run() and Svc().go() is None\n",
    }

    def graph(self, cache=None):
        return builtin_graph([sf(p, 1, t, test=p.startswith("tests/"), blob="blob-" + p) for p, t in self.FILES.items()], cache)

    def test_imports_resolve_by_path(self):
        g = self.graph()
        self.assertEqual(g.links["src/pkg/core.py"], {"src/pkg/util.py"})              # relative; stdlib json skipped
        self.assertEqual(g.links["src/pkg/__init__.py"], {"src/pkg/core.py"})
        self.assertEqual(g.links["tests/test_core.py"], {"src/pkg/__init__.py", "src/pkg/core.py"})  # src layout
        self.assertEqual(g.links["lambdas/x/handler.py"], {"lambdas/x/utils.py"})      # sibling, not other/deep
        self.assertFalse(g.has_flows)
        self.assertEqual(g.stats["source"], "built-in ast")

    def test_tests_link_to_the_functions_they_import_and_name(self):
        g = self.graph()
        self.assertEqual(g.tested["src/pkg/core.py::run"], {"tests/test_core.py"})      # through the __init__ re-export
        self.assertEqual(g.tested["src/pkg/core.py::Svc.go"], {"tests/test_core.py"})
        self.assertNotIn("src/pkg/util.py::helper", g.tested)
        self.assertEqual(g.nodes["src/pkg/core.py::Svc.go"], ("Function", "src/pkg/core.py", False))

    def test_per_file_syntax_is_cached(self):
        cache = {}
        self.graph(cache)
        self.assertEqual(len(cache), len(self.FILES))
        before = dict(cache)
        self.graph(cache)
        self.assertEqual(cache, before)


if __name__ == "__main__":
    unittest.main()
