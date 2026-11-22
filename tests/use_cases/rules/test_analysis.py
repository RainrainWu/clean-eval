"""Reading code (use_cases/rules/analysis.py): classifying paths, splitting modules, graph queries, per-file
signals, and the built-in knowledge graph."""

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Graph
from clean_eval.use_cases.rules import analysis
from clean_eval.use_cases.rules.analysis import (
    builtin_graph,
    file_signals,
    module_of,
    python_stats,
    split_modules,
    write_calls,
)
from tests.domain.builders import sf

# Classifying paths and modules (use_cases/rules/analysis.py): tests, scales, tooling, exclusions.

P = Policy()


def test_tests_scales_manifests_and_names():
    assert analysis.is_test("tests/unit/test_x.py", P)
    assert analysis.is_test("src/conftest.py", P)
    assert not analysis.is_test("src/testing_utils.py", P)
    assert [analysis.classify_scale("tests/%s/test_a.py" % d) for d in ("e2e", "integration", "unit")] == [
        "e2e",
        "integration",
        "unit",
    ]
    for name in ("terraform/lambda_src", ".github/scripts", "k8s/overlays", "src/testing"):
        assert analysis.not_applicable(name, P) is not None, name
    assert analysis.not_applicable("src/api", P) is None
    assert analysis.has_core_keyword("src/ingestion", P)
    assert not analysis.has_core_keyword("src/helpers", P)
    assert analysis.looks_like_tooling("scripts/x", "scripts/x/a.py", P)
    assert analysis.is_migration("db/alembic/versions/001.py", P)


@pytest.mark.parametrize(
    "path", ["README.md", "pkg/README.rst", "CHANGELOG.md", "LICENSE", "docs/guide/x.py", "img/a.png"]
)
def test_docs_and_assets_are_ignored(path):
    assert analysis.ignored(path, P)


@pytest.mark.parametrize(
    "path",
    [
        "requirements.txt",
        "SKILL.md",
        ".claude/skills/x/SKILL.md",
        "CLAUDE.md",
        "AGENTS.md",
        ".clean-eval/review-rules.md",
    ],
)
def test_dependencies_agent_instructions_and_review_rules_carry_risk(path):
    assert not analysis.ignored(path, P)  # they steer behaviour


def test_excluded_paths():
    p = Policy.from_overrides({"exclude": ["vendor/*"]})
    assert analysis.excluded("vendor/lib/x.py", p)
    assert not analysis.excluded("src/x.py", p)


# Splitting a repository into modules (use_cases/rules/analysis.py), and finding the module of a path.


def test_large_module_is_split_into_children():
    mods, notes = split_modules([sf("a/b/x.py", 400), sf("a/c/y.py", 400), sf("z/w.py", 100)], P, depth=2)
    assert sorted(mods) == ["a/b", "a/c", "z"]
    assert any("split a" in n for n in notes)


def test_single_child_chain_is_skipped():
    mods, _ = split_modules(
        [sf("pkg/src/pkg/one/a.py", 300), sf("pkg/src/pkg/two/b.py", 300), sf("other/c.py", 50)], P, depth=2
    )
    assert "pkg/src/pkg/one" in mods
    assert "pkg/src/pkg/two" in mods


def test_depth_and_cap():
    files = [
        sf("src/ext/a.py", 100),
        sf("src/ext/b.py", 100),
        sf("src/ext/c.py", 100),
        sf("src/dom/d.py", 60),
        sf("src/dom/e.py", 60),
    ]
    assert sorted(split_modules(files, P, depth=1)[0]) == ["src"]
    assert sorted(split_modules(files, P, depth=2)[0]) == ["src/dom", "src/ext"]
    assert sorted(split_modules(files, P)[0]) == ["src/dom", "src/ext/a.py", "src/ext/b.py", "src/ext/c.py"]
    assert sorted(split_modules(files, P, depth=3, max_modules=2)[0]) == ["src/dom", "src/ext"]


def test_module_of_finds_the_deepest_module():
    names = {"src/api", "src/ext/a.py", "src/core/(files)", "src/core/deep", "(root)", "tools"}
    assert module_of("src/api/handlers/x.py", names) == "src/api"
    assert module_of("src/ext/a.py", names) == "src/ext/a.py"
    assert module_of("src/ext/new.py", names) is None  # a new file in a folder split per file
    assert module_of("src/core/c.py", names) == "src/core/(files)"
    assert module_of("src/core/deep/d.py", names) == "src/core/deep"
    assert module_of("setup.py", names) == "(root)"
    assert module_of("tools/x/y.sh", names) == "tools"
    assert module_of("terraform/main.tf", names) is None
    assert module_of("setup.py", {"src"}) is None


# Queries over the knowledge graph (use_cases/rules/analysis.py): dependents, functions and test files by module.


def test_dependents_functions_and_test_files_by_module():
    nodes = {
        "a.py::f": ("Function", "a.py", False),
        "b.py::g": ("Function", "b.py", False),
        "c.py::t": ("Function", "c.py", True),
    }
    g = Graph(nodes=nodes, links={"a.py": {"b.py"}}, tested={"a.py::f": {"tests/test_a.py"}}, flows=[], stats={})
    file_mod = {"a.py": "A", "b.py": "B", "c.py": "C"}
    fan_in, fan_out = analysis.dependents(g, file_mod)
    assert (fan_in["B"], fan_out["A"]) == ({"A"}, {"B"})
    assert sorted(analysis.functions_by_module(g, file_mod)) == ["A", "B"]
    assert analysis.test_files_to_modules(g, file_mod) == {"tests/test_a.py": {"A"}}


# What one Python file says about risk (use_cases/rules/analysis.py): ast metrics, write calls, TODOs.

SAMPLE = """
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
"""


def test_python_stats():
    st = python_stats(SAMPLE)
    assert st is not None
    assert (len(st.funcs), st.typed, st.broad, st.swallow) == (2, 1, 1, 1)
    assert st.funcs[0][1] == 3  # one if and one boolean operator on top of the base path
    assert python_stats("def broken(:") is None


def test_write_calls_are_counted_with_a_line_and_gated_by_context():
    aws = "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"
    assert write_calls(aws)["aws_write"] == (2, 3)
    assert "aws_write" not in write_calls("x.put_object(1)\n")  # no boto import: not an AWS write


def test_signals_are_cached_per_content():
    cache = {}
    a = file_signals(sf("a.py", 1, "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", blob="b1"), cache)
    assert a.todo == 2
    assert file_signals(sf("renamed.py", 1, "ignored", blob="b1"), cache) is a


# The built-in knowledge graph (use_cases/rules/analysis.py): imports resolved by path, and tests linked to the
# functions they import and name.

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
