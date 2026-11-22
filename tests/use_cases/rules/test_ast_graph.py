"""The built-in knowledge graph (use_cases/rules/ast_graph.py): imports resolved by path, and tests linked to the
functions they import and name."""

from clean_eval.use_cases.rules.ast_graph import builtin_graph
from tests.domain.builders import sf

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


def graph(cache=None):
    return builtin_graph([sf(p, 1, t, test=p.startswith("tests/"), blob="blob-" + p) for p, t in FILES.items()], cache)


def test_imports_resolve_by_path():
    g = graph()
    assert g.links["src/pkg/core.py"] == {"src/pkg/util.py"}  # relative; stdlib json skipped
    assert g.links["src/pkg/__init__.py"] == {"src/pkg/core.py"}
    assert g.links["tests/test_core.py"] == {"src/pkg/__init__.py", "src/pkg/core.py"}  # src layout
    assert g.links["lambdas/x/handler.py"] == {"lambdas/x/utils.py"}  # sibling, not other/deep
    assert not g.has_flows
    assert g.stats["source"] == "built-in ast"


def test_tests_link_to_the_functions_they_import_and_name():
    g = graph()
    assert g.tested["src/pkg/core.py::run"] == {"tests/test_core.py"}  # through the __init__ re-export
    assert g.tested["src/pkg/core.py::Svc.go"] == {"tests/test_core.py"}
    assert "src/pkg/util.py::helper" not in g.tested
    assert g.nodes["src/pkg/core.py::Svc.go"] == ("Function", "src/pkg/core.py", False)


def test_per_file_syntax_is_cached():
    cache = {}
    graph(cache)
    assert len(cache) == len(FILES)
    before = dict(cache)
    graph(cache)
    assert cache == before
