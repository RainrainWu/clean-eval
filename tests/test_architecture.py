"""The dependency rule of Clean Architecture, enforced on the imports: domain <- use_cases <- adaptor <- external.
Inner layers know nothing of outer ones and do no I/O. The domain holds data models only: pydantic models whose
invariants are validators, plus the constants and error types they use. The use_cases layer holds the abstract classes
of every external utility the business logic uses (interfaces.py) and module-level functions that take implementations
of them; the business rules are in use_cases/rules. Everything that reaches outside the process does so in the external
layer, each dependency through its one gateway, which implements its interface. Every import is absolute. The tests are
laid out like the package, follow the same dependency rule, and are written for pytest.

The rules read the code through the `codebase` fixture (tests.architecture.Codebase)."""

import ast
import fnmatch
import inspect
from abc import ABC

from pydantic import BaseModel

TOP = "clean_eval"
LAYERS = {  # layer -> the layers it may import from
    "domain": {"domain"},
    "use_cases": {"domain", "use_cases"},
    "adaptor": {"domain", "use_cases", "adaptor"},
    "external": {"domain", "use_cases", "adaptor", "external"},
}
PURE_ONLY = {"domain", "use_cases"}
# computation only: no files, network, environment, processes or clock
PURE_IMPORTS = {
    "__future__",
    "abc",
    "ast",
    "base64",
    "collections",
    "copy",
    "dataclasses",
    "datetime",
    "enum",
    "fnmatch",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "re",
    "sys",
    "typing",
    "pydantic",
    "pydantic_core",
}
PURE_SYS = {"stdlib_module_names"}  # all the inner layers may take from sys: a constant
# Standard modules that reach outside the process (processes, files, databases, the network, the environment).
REACHING_OUT = {
    "subprocess",
    "sqlite3",
    "os",
    "shutil",
    "filecmp",
    "pathlib",
    "tempfile",
    "glob",
    "io",
    "socket",
    "ssl",
    "urllib",
    "http",
    "ftplib",
    "smtplib",
    "multiprocessing",
    "ctypes",
    "webbrowser",
}
# Each external dependency has one gateway class; a gateway module is the only module allowed what reaches the
# dependencies of its gateways. module -> (what it may import that reaches outside, its gateway classes)
GATEWAYS = {
    "external/git.py": ({"subprocess"}, {"GitGateway"}),  # the git CLI
    "external/github.py": ({"subprocess"}, {"GitHubGateway"}),  # the gh CLI
    "external/code_review_graph.py": ({"subprocess", "sqlite3"}, {"CodeReviewGraphGateway"}),  # its CLI and database
    "external/filesystem.py": ({"os", "shutil", "filecmp", "pathlib"}, {"FileSystemGateway"}),  # files and folders
    "external/environment.py": ({"os", "datetime"}, {"EnvironmentGateway", "ClockGateway"}),  # variables, today's date
    "external/claude_code.py": ({"subprocess", "shutil", "os"}, {"ClaudeCodeGateway"}),  # the CI agent: its CLI and npm
}
# The interfaces (use_cases/interfaces.py) and the one class implementing each: a gateway or a presenter.
INTERFACES = "use_cases/interfaces.py"
IMPLEMENTATIONS = {
    "Git": "external/git.py",
    "CodeHost": "external/github.py",
    "FileSystem": "external/filesystem.py",
    "KnowledgeGraph": "external/code_review_graph.py",
    "Environment": "external/environment.py",
    "Clock": "external/environment.py",
    "Agent": "external/claude_code.py",
    "DiagramPresenter": "adaptor/presenters.py",
    "ReportPresenter": "adaptor/presenters.py",
    "RunLog": "external/cli.py",  # a CI run's log is the console, the driver's
}
DRIVERS = {"external/cli.py", "external/agent_guard.py"}  # the console is theirs (they are driven by it)
BOOTSTRAP = {"__main__.py"}  # puts the checkout on sys.path before any of the package can be imported
# A layer's test infrastructure, not tests: a package's __init__.py and a conftest.py in any test folder, and fakes.py.
TEST_SUPPORT = {"__init__.py", "conftest.py", "fakes.py"}
# Each folder of the package, and the layer of the review rules that must cover it.
# Code the convention and style rules must reject (see their able-to-fail checks).
PLANTED_TEST_MODULE = """\"\"\"A test module.\"\"\"
import pytest

TABLE = [1]
lowercase = 2
pytestmark = pytest.mark.e2e

def helper(
    value,
):
    return value


@pytest.fixture
def fixture_in_a_test_module(
):
    return 1


def test_outside_a_class(
):
    pass


class Helper:
    pass


class TestThing:
    \"\"\"Things.\"\"\"

    def helper(
        self,
    ):
        pass

    @pytest.fixture
    def test_that_is_a_fixture(
        self,
    ):
        pass

    def test_it(
        self,
    ):
        pass
"""
PLANTED_CONFTEST = """import pytest

CONSTANT = 1


@pytest.fixture
def thing(
):
    return 1


def helper(
):
    return 2


class Support:
    pass


def pytest_configure(
    config,
):
    pass
"""
PLANTED_SUPPORT = """import pytest


def helper(
):
    pass


class Support:
    \"\"\"A support class.\"\"\"

    @pytest.fixture
    def fixture_in_support(
        self,
    ):
        pass
"""
PLANTED_STYLE = """class Thing:
    def _protected(self, x):
        return x

    def __init__(
        self,
    ):
        def one_line(y, z): return z

    @staticmethod
    def __private(
        value,
    ):
        return lambda m: m
"""
REVIEW_RULES_LAYERS = {
    "domain": "domain",
    "use_cases": "application",
    "use_cases/rules": "application",
    "adaptor": "interface",
    "external": "infrastructure",
}


class TestDependencyRule:
    """Every module is in a layer, a layer imports only from itself and the inner ones, the inner two do no I/O and
    never read the clock, and every import is absolute."""

    def test_every_module_belongs_to_a_layer(
        self,
        codebase,
    ):
        top = {path.name for path in codebase.package.iterdir() if path.is_dir() and path.name != "__pycache__"}
        assert top == set(LAYERS)
        assert {path.name for path in codebase.package.glob("*.py")} == {"__init__.py", "__main__.py"}

    def test_layers_only_import_inward(
        self,
        codebase,
    ):
        for layer, allowed in LAYERS.items():
            for path in codebase.layer_files(layer):
                for name in codebase.imports(path):
                    parts = name.split(".")
                    if parts[0] == TOP and len(parts) > 1 and parts[1] in LAYERS:
                        assert parts[1] in allowed, "%s imports %s" % (path.relative_to(codebase.package), name)

    def test_domain_and_use_cases_do_no_io(
        self,
        codebase,
    ):
        for layer in PURE_ONLY:
            for path in codebase.layer_files(layer):
                for name in codebase.imports(path):
                    top = name.split(".")[0]
                    if top != TOP:
                        assert top in PURE_IMPORTS, "%s imports %s" % (path.relative_to(codebase.package), name)

    def test_domain_and_use_cases_take_only_constants_from_sys(
        self,
        codebase,
    ):
        for layer in PURE_ONLY:
            for path in codebase.layer_files(layer):
                for name, line in codebase.sys_uses(path):
                    assert name in PURE_SYS, "%s:%d" % (path.relative_to(codebase.package), line)

    def test_domain_and_use_cases_never_read_the_clock(
        self,
        codebase,
    ):
        for layer in PURE_ONLY:
            for path in codebase.layer_files(layer):
                assert codebase.clock_reads(path) == [], path.relative_to(codebase.package)

    def test_every_import_is_absolute(
        self,
        codebase,
    ):
        scripts = sorted((codebase.root / ".github" / "scripts").glob("*.py"))
        for path in codebase.package_files() + codebase.all_test_files() + scripts:
            assert codebase.relative_imports(path) == [], path.relative_to(codebase.root)

    def test_the_launcher_only_reaches_the_external_layer(
        self,
        codebase,
    ):
        names = [name for name in codebase.imports(codebase.package / "__main__.py") if name.startswith(TOP + ".")]
        assert names == ["clean_eval.external.cli"]

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
    ):
        package = codebase.package
        assert "clean_eval.domain.policy" in codebase.imports(package / "use_cases" / "rules" / "heatmap.py")
        assert "clean_eval.use_cases.heatmap" in codebase.imports(package / "external" / "cli.py")
        relative = "from ..domain import x\nimport os\nfrom . import y\nfrom a import b\n"
        assert codebase.relative_imports(relative) == [1, 3]
        assert [name for name, _ in codebase.sys_uses(package / "external" / "agent_guard.py")] == ["stdin", "stderr"]
        assert any(name.startswith("clean_eval.adaptor") for name in codebase.imports(package / "external" / "cli.py"))
        assert "os" in [name.split(".")[0] for name in codebase.imports(package / "external" / "filesystem.py")]
        assert codebase.data_model_violations(
            "def score(value):\n    return value\nclass M(BaseModel):\n    def rate(self):\n        pass\nprint(1)\n"
        ) == ["function score", "method M.rate", "statement on line 6"]


class TestExternalDependenciesStayInTheirGateways:
    """What reaches outside the process (processes, files, the environment, the clock, the console) is reached only by
    the gateway of that dependency, or by the drivers for the console."""

    def test_only_a_gateway_imports_what_reaches_outside_the_process(
        self,
        codebase,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            if rel in BOOTSTRAP:
                continue
            reaching = {name.split(".")[0] for name in codebase.imports(path)} & (
                REACHING_OUT | {"datetime"} if rel.startswith("external/") else REACHING_OUT
            )
            assert reaching <= GATEWAYS.get(rel, (set(), set()))[0], rel

    def test_a_gateway_module_holds_just_its_gateway_classes(
        self,
        codebase,
    ):
        for rel, (_, gateways) in GATEWAYS.items():
            tree = codebase.parse(codebase.package / rel)
            classes = {
                node.name for node in tree.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
            }
            assert classes == gateways, rel
            assert all(name.endswith("Gateway") for name in classes), rel

    def test_files_the_clock_and_the_environment_are_reached_only_through_their_gateways(
        self,
        codebase,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            for callee, line in codebase.calls(path):
                if callee == "open":
                    assert rel == "external/filesystem.py", "%s:%d opens a file" % (rel, line)
            if rel != "external/environment.py":
                assert codebase.clock_reads(path) == [], "%s reads the clock" % rel
            if rel != "external/environment.py":
                for node in codebase.nodes(path, ast.Attribute):
                    assert node.attr not in {"environ", "getenv", "putenv"}, "%s:%d" % (rel, node.lineno)

    def test_only_the_drivers_use_the_console(
        self,
        codebase,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            if rel in DRIVERS:
                continue
            for callee, line in codebase.calls(path):
                assert callee not in {"print", "input"}, "%s:%d" % (rel, line)
            for node in codebase.nodes(path, ast.Attribute):
                if isinstance(node.value, ast.Name) and node.value.id == "sys":
                    assert node.attr not in {"stdin", "stdout", "stderr", "exit", "argv"}, "%s:%d" % (rel, node.lineno)

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
    ):
        package = codebase.package
        assert "subprocess" in {name.split(".")[0] for name in codebase.imports(package / "external" / "git.py")}
        assert codebase.clock_reads(package / "external" / "environment.py")  # the one clock read is found
        assert "open" in {callee for callee, _ in codebase.calls(package / "external" / "filesystem.py")}


class TestUseCasesHoldInterfacesAndFunctions:
    """The use_cases layer holds two things: the abstract classes of every external utility the business logic uses
    (interfaces.py), implemented by the outer layers, and module-level functions that take those implementations as
    parameters and call only the abstract methods."""

    def test_the_interfaces_are_abstract_classes_whose_methods_are_all_abstract(
        self,
        codebase,
    ):
        module = codebase.package / INTERFACES
        functions = [
            node.name
            for node in codebase.parse(module).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert functions == []
        interfaces = codebase.classes(module)
        assert set(interfaces) == set(IMPLEMENTATIONS)
        for name, interface in interfaces.items():
            assert issubclass(interface, ABC), name
            assert codebase.public_methods(interface) == set(interface.__abstractmethods__), name

    def test_the_rest_of_the_layer_is_module_level_functions(
        self,
        codebase,
    ):
        for path in codebase.layer_files("use_cases"):
            if path != codebase.package / INTERFACES:
                classes = [node.name for node in codebase.nodes(path, ast.ClassDef)]
                assert classes == [], path.relative_to(codebase.package)

    def test_each_interface_has_one_implementation_with_nothing_more(
        self,
        codebase,
    ):
        for name, interface in codebase.classes(codebase.package / INTERFACES).items():
            rel = IMPLEMENTATIONS[name]
            classes = [cls for cls in codebase.classes(codebase.package / rel).values() if issubclass(cls, interface)]
            assert len(classes) == 1, rel
            assert not inspect.isabstract(classes[0]), rel  # every abstract method implemented
            # and nothing the interface doesn't declare: what the business logic can use is what it defined
            assert codebase.public_methods(classes[0]) <= set(interface.__abstractmethods__), rel

    def test_every_gateway_implements_an_interface(
        self,
    ):
        assert set(GATEWAYS) <= set(IMPLEMENTATIONS.values())


class TestDomainHoldsDataModels:
    """The domain holds data models only: no functions, no methods but validators, computed fields, properties and
    constructors, and no class that is neither a pydantic model nor an error."""

    def test_no_functions_and_no_logic_methods(
        self,
        codebase,
    ):
        for path in codebase.layer_files("domain"):
            if path.name != "__init__.py":
                assert codebase.data_model_violations(path) == [], path.name

    def test_every_class_is_a_pydantic_model_or_an_error(
        self,
        codebase,
    ):
        seen = 0
        for path in codebase.layer_files("domain"):
            if path.name != "__init__.py":
                for name, cls in codebase.classes(path).items():
                    seen += 1
                    assert issubclass(cls, (BaseModel, Exception)), "%s.%s" % (path.name, name)
        assert seen > 30


class TestsMirrorThePackage:
    """tests/<layer>/ holds the tests of clean_eval/<layer>/, test_<module>.py for <module>.py (plus test support: a
    conftest.py in any test folder, and fakes.py), and follows the dependency rule: a layer's tests import only that
    layer and the inner ones, from the package and from the tests alike. Tests that span the layers sit at the top of
    tests/. Every test is a pytest test: from unittest, only unittest.mock, whose MagicMocks the tests inject."""

    def test_each_test_module_names_the_module_it_tests(
        self,
        codebase,
    ):
        for _, path in codebase.layer_tests(LAYERS):
            if path.name not in TEST_SUPPORT:
                where = path.relative_to(codebase.root)
                assert path.name.startswith("test_"), "%s is neither a test module nor test support" % where
                assert codebase.module_under_test(path).is_file(), "%s names no module of the package" % where

    def test_every_test_folder_is_a_package(
        self,
        codebase,
    ):  # the layers' test modules share names; tests import tests.*
        for folder in {path.parent for path in codebase.all_test_files()}:
            assert (folder / "__init__.py").is_file(), folder.relative_to(codebase.root)

    def test_a_layers_tests_import_only_that_layer_and_inner_ones(
        self,
        codebase,
    ):
        for layer, path in codebase.layer_tests(LAYERS):
            for name in codebase.imports(path):
                parts = name.split(".")
                if parts[0] in (TOP, "tests") and len(parts) > 1 and parts[1] in LAYERS:
                    assert parts[1] in LAYERS[layer], "%s imports %s" % (path.relative_to(codebase.root), name)

    def test_the_tests_use_pytest_and_from_unittest_only_its_mock(
        self,
        codebase,
    ):
        for path in codebase.all_test_files():
            assert codebase.unittest_beyond_mock(path) == [], path.relative_to(codebase.root)

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
    ):
        unittest_test = "import unittest\nfrom unittest import TestCase, mock\nfrom unittest.mock import MagicMock\n"
        assert codebase.unittest_beyond_mock(unittest_test) == ["unittest", "unittest.TestCase"]
        assert not codebase.module_under_test(codebase.tests / "use_cases" / "test_nothing.py").is_file()
        gateway_test = "from clean_eval.external.git import GitGateway\nfrom tests.use_cases.fakes import MemoryGit\n"
        assert {"tests.use_cases.fakes", "clean_eval.external.git"} <= set(codebase.imports(gateway_test))
        assert ("external", codebase.tests / "external" / "test_git.py") in codebase.layer_tests(LAYERS)


class TestsFollowTheConventions:
    """The shape tests/AGENTS.md ("Writing tests") gives the tests: a test module holds constants and Test* classes of
    test methods, every fixture lives in a conftest.py, and the tests' support holds classes and constants only."""

    def test_test_modules_hold_constants_and_test_classes(
        self,
        codebase,
    ):
        for path in codebase.test_modules():
            assert codebase.test_module_violations(path) == [], path.relative_to(codebase.root)

    def test_a_conftest_holds_fixtures_hooks_and_constants(
        self,
        codebase,
    ):
        for path in codebase.conftests():
            assert codebase.conftest_violations(path) == [], path.relative_to(codebase.root)

    def test_support_holds_classes_and_constants(
        self,
        codebase,
    ):
        for path in codebase.support_modules():
            assert codebase.support_violations(path) == [], path.relative_to(codebase.root)

    def test_the_rules_are_able_to_fail(
        self,
        codebase,
    ):
        assert codebase.test_module_violations(PLANTED_TEST_MODULE) == [
            "5 lowercase",
            "8 helper",
            "15 fixture_in_a_test_module",
            "20 test_outside_a_class",
            "25 Helper",
            "32 TestThing.helper",
            "38 TestThing.test_that_is_a_fixture",
        ]
        assert codebase.conftest_violations(PLANTED_CONFTEST) == ["12 helper", "17 Support"]
        assert codebase.support_violations(PLANTED_SUPPORT) == ["4 helper", "13 fixture_in_support"]


class TestCodeStyle:
    """The code base's style (AGENTS.md, "Code style"), in the package, the tests and the scripts alike: no
    protected-level functions or methods, each parameter on a line of its own, and no single-character parameter
    names."""

    def test_no_function_or_method_is_protected(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.protected_definitions(path) == [], path.relative_to(codebase.root)

    def test_each_parameter_has_a_line_of_its_own(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.shared_parameter_lines(path) == [], path.relative_to(codebase.root)

    def test_parameter_names_are_more_than_one_character(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.single_character_parameters(path) == [], path.relative_to(codebase.root)

    def test_the_rules_are_able_to_fail(
        self,
        codebase,
    ):
        assert codebase.protected_definitions(PLANTED_STYLE) == ["2 _protected", "11 __private"]
        assert codebase.shared_parameter_lines(PLANTED_STYLE) == ["2 _protected", "8 one_line"]
        assert codebase.single_character_parameters(PLANTED_STYLE) == [
            "2 _protected(x)",
            "8 one_line(y)",
            "8 one_line(z)",
            "14 lambda(m)",
        ]


class TestReviewRulesCoverThisPackage:
    """The default review rules must place every clean_eval layer in the matching rules layer, so the dependency rules
    (CA-01, CA-02) apply to this repository's own code in reviews."""

    def test_each_package_layer_maps_to_its_rules_layer(
        self,
        codebase,
    ):
        layers = codebase.review_rules_layers()
        for folder, expected in REVIEW_RULES_LAYERS.items():
            sample = "clean_eval/%s/x.py" % folder
            matched = [
                name for name, globs in layers.items() if any(fnmatch.fnmatch(sample, pattern) for pattern in globs)
            ]
            assert matched == [expected], sample
