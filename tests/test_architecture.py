"""The dependency rule of Clean Architecture, enforced on the imports: domain <- use_cases <- adaptor <- external.
Inner layers know nothing of outer ones and do no I/O. The domain holds data models only: pydantic models whose
invariants are validators, plus the constants and error types they use. The use_cases layer holds the abstract classes
of every external utility the business logic uses (interfaces.py) and module-level functions that take implementations
of them; the business rules are in use_cases/rules. Everything that reaches outside the process does so in the external
layer, each dependency through its one gateway, which implements its interface. Beside the layers, settings.py holds a
run's settings: every layer but the domain may import them, they import no layer, and only the drivers build them
(reading the environment). Every import is absolute. The tests are laid out like the package, follow the same
dependency rule, and are written for pytest.

The rules read the code through the `codebase` fixture (tests.architecture.Codebase) and hold it to the tables of the
`architecture` fixture (the layers, the gateways, the interfaces' implementations, ...); their able-to-fail checks plant
code a rule must reject (the `planted_*` fixtures)."""

import ast
import fnmatch
import inspect
from abc import ABC
from enum import Enum

from pydantic import BaseModel


class TestDependencyRule:
    """Every module is in a layer, a layer imports only from itself and the inner ones, the inner two do no I/O and
    never read the clock, and every import is absolute."""

    def test_every_module_belongs_to_a_layer(
        self,
        codebase,
        architecture,
    ):
        top = {path.name for path in codebase.package.iterdir() if path.is_dir() and path.name != "__pycache__"}
        assert top == set(architecture.layers)
        assert {path.name for path in codebase.package.glob("*.py")} == architecture.root_modules

    def test_layers_only_import_inward(
        self,
        codebase,
        architecture,
    ):
        for layer, allowed in architecture.layers.items():
            for path in codebase.layer_files(layer):
                for name in codebase.imports(path):
                    parts = name.split(".")
                    if parts[0] == codebase.top and len(parts) > 1 and parts[1] in architecture.layers:
                        assert parts[1] in allowed, "%s imports %s" % (path.relative_to(codebase.package), name)

    def test_domain_and_use_cases_do_no_io(
        self,
        codebase,
        architecture,
    ):
        for layer in architecture.pure_layers:
            for path in codebase.layer_files(layer):
                for name in codebase.imports(path):
                    top = name.split(".")[0]
                    if top != codebase.top:
                        where = path.relative_to(codebase.package)
                        assert top in architecture.pure_imports, "%s imports %s" % (where, name)

    def test_domain_and_use_cases_take_only_constants_from_sys(
        self,
        codebase,
        architecture,
    ):
        for layer in architecture.pure_layers:
            for path in codebase.layer_files(layer):
                for name, line in codebase.sys_uses(path):
                    assert name in architecture.pure_sys, "%s:%d" % (path.relative_to(codebase.package), line)

    def test_domain_and_use_cases_never_read_the_clock(
        self,
        codebase,
        architecture,
    ):
        for layer in architecture.pure_layers:
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
        launcher = codebase.package / "__main__.py"
        names = [name for name in codebase.imports(launcher) if name.startswith(codebase.top + ".")]
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
        architecture,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            if rel in architecture.bootstrap:
                continue
            reaching = {name.split(".")[0] for name in codebase.imports(path)} & (
                architecture.reaching_out | {"datetime"} if rel.startswith("external/") else architecture.reaching_out
            )
            allowed = (
                architecture.settings_reach
                if rel == architecture.settings_module
                else architecture.gateways.get(rel, (set(), set()))[0]
            )
            assert reaching <= allowed, rel

    def test_a_gateway_module_holds_just_its_gateway_classes(
        self,
        codebase,
        architecture,
    ):
        for rel, (_, gateways) in architecture.gateways.items():
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
        architecture,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            if rel in architecture.drivers:
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


class TestTheSettings:
    """A run's settings sit beside the layers: they import no layer, every layer but the domain may import them, and
    only the drivers build them, since building them reads the environment."""

    def test_the_settings_import_no_layer(
        self,
        codebase,
        architecture,
    ):
        imports = codebase.imports(codebase.package / architecture.settings_module)
        assert [name for name in imports if name.startswith(codebase.top + ".")] == []

    def test_the_domain_does_not_import_the_settings(
        self,
        codebase,
    ):
        for path in codebase.layer_files("domain"):
            assert codebase.top + ".settings" not in codebase.imports(path), path.relative_to(codebase.package)

    def test_only_the_drivers_build_the_settings(
        self,
        codebase,
        architecture,
    ):
        for path in codebase.package_files():
            rel = path.relative_to(codebase.package).as_posix()
            if rel not in architecture.drivers | {architecture.settings_module}:
                assert codebase.settings_built(path) == [], rel

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
        architecture,
    ):
        assert codebase.settings_built(codebase.package / "external" / "cli.py")
        planted = "from clean_eval.settings import Settings\ns = Settings()\nt = Settings.model_validate({})\n"
        assert codebase.settings_built(planted) == [2, 3]
        imports = codebase.imports(codebase.package / architecture.settings_module)
        assert "pydantic_settings" in {name.split(".")[0] for name in imports}


class TestEachConstantHasOneHome:
    """A name the package binds at the top of a module is bound in that one module, and the modules that need it import
    it: two copies of a constant drift apart (as two MAX_PATCH_CHARS could)."""

    def test_no_name_is_bound_in_two_modules(
        self,
        codebase,
    ):
        modules = codebase.package_files()
        shared = codebase.shared_definitions(modules)
        homes = {name: [str(modules[i].relative_to(codebase.package)) for i in where] for name, where in shared.items()}
        assert homes == {}

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
    ):
        planted = ["LIMIT = 1\nSOLE = 2\n", "A, LIMIT = 3, 4\n", "type LIMIT = int\n", "OTHER: int = 5\n"]
        assert codebase.shared_definitions(planted) == {"LIMIT": [0, 1, 2]}


class TestUseCasesHoldInterfacesAndFunctions:
    """The use_cases layer holds two things: the abstract classes of every external utility the business logic uses
    (interfaces.py), implemented by the outer layers, and module-level functions that take those implementations as
    parameters and call only the abstract methods."""

    def test_the_interfaces_are_abstract_classes_whose_methods_are_all_abstract(
        self,
        codebase,
        architecture,
    ):
        module = codebase.package / architecture.interfaces_module
        functions = [
            node.name
            for node in codebase.parse(module).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert functions == []
        interfaces = codebase.classes(module)
        assert set(interfaces) == set(architecture.implementations)
        for name, interface in interfaces.items():
            assert issubclass(interface, ABC), name
            assert codebase.public_methods(interface) == set(interface.__abstractmethods__), name

    def test_the_rest_of_the_layer_is_module_level_functions(
        self,
        codebase,
        architecture,
    ):
        for path in codebase.layer_files("use_cases"):
            if path != codebase.package / architecture.interfaces_module:
                classes = [node.name for node in codebase.nodes(path, ast.ClassDef)]
                assert classes == [], path.relative_to(codebase.package)

    def test_each_interface_has_one_implementation_with_nothing_more(
        self,
        codebase,
        architecture,
    ):
        for name, interface in codebase.classes(codebase.package / architecture.interfaces_module).items():
            rel = architecture.implementations[name]
            classes = [cls for cls in codebase.classes(codebase.package / rel).values() if issubclass(cls, interface)]
            assert len(classes) == 1, rel
            assert not inspect.isabstract(classes[0]), rel  # every abstract method implemented
            # and nothing the interface doesn't declare: what the business logic can use is what it defined
            assert codebase.public_methods(classes[0]) <= set(interface.__abstractmethods__), rel

    def test_every_gateway_implements_an_interface(
        self,
        architecture,
    ):
        assert set(architecture.gateways) <= set(architecture.implementations.values())


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

    def test_every_class_is_a_pydantic_model_an_enumeration_or_an_error(
        self,
        codebase,
    ):
        seen = 0
        for path in codebase.layer_files("domain"):
            if path.name != "__init__.py":
                for name, cls in codebase.classes(path).items():
                    seen += 1
                    assert issubclass(cls, (BaseModel, Enum, Exception)), "%s.%s" % (path.name, name)
        assert seen > 30


class TestsMirrorThePackage:
    """tests/<layer>/ holds the tests of clean_eval/<layer>/, test_<module>.py for <module>.py (plus test support: a
    conftest.py in any test folder, and fakes.py), and follows the dependency rule: a layer's tests import only that
    layer and the inner ones, from the package and from the tests alike. Tests that span the layers sit at the top of
    tests/. Every test is a pytest test: from unittest, only unittest.mock, whose MagicMocks the tests inject."""

    def test_each_test_module_names_the_module_it_tests(
        self,
        codebase,
        architecture,
    ):
        for _, path in codebase.layer_tests(architecture.layers):
            if path.name not in architecture.test_support:
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
        architecture,
    ):
        for layer, path in codebase.layer_tests(architecture.layers):
            for name in codebase.imports(path):
                parts = name.split(".")
                if parts[0] in (codebase.top, "tests") and len(parts) > 1 and parts[1] in architecture.layers:
                    where = path.relative_to(codebase.root)
                    assert parts[1] in architecture.layers[layer], "%s imports %s" % (where, name)

    def test_the_tests_use_pytest_and_from_unittest_only_its_mock(
        self,
        codebase,
    ):
        for path in codebase.all_test_files():
            assert codebase.unittest_beyond_mock(path) == [], path.relative_to(codebase.root)

    def test_the_rule_is_able_to_fail(
        self,
        codebase,
        architecture,
    ):
        unittest_test = "import unittest\nfrom unittest import TestCase, mock\nfrom unittest.mock import MagicMock\n"
        assert codebase.unittest_beyond_mock(unittest_test) == ["unittest", "unittest.TestCase"]
        assert not codebase.module_under_test(codebase.tests / "use_cases" / "test_nothing.py").is_file()
        gateway_test = "from clean_eval.external.git import GitGateway\nfrom tests.use_cases.fakes import MemoryGit\n"
        assert {"tests.use_cases.fakes", "clean_eval.external.git"} <= set(codebase.imports(gateway_test))
        assert ("external", codebase.tests / "external" / "test_git.py") in codebase.layer_tests(architecture.layers)


class TestsFollowTheConventions:
    """The shape tests/AGENTS.md ("Writing tests") gives the tests: a test module holds Test* classes of test methods
    and no module-level value (its static assets are fixtures), every fixture lives in a conftest.py, and the tests'
    support holds classes and constants only."""

    def test_test_modules_hold_only_test_classes(
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
        planted_test_module,
        planted_conftest,
        planted_support,
    ):
        assert codebase.test_module_violations(planted_test_module) == [
            "4 TABLE",
            "5 lowercase",
            "8 helper",
            "15 fixture_in_a_test_module",
            "20 test_outside_a_class",
            "25 Helper",
            "32 TestThing.helper",
            "38 TestThing.test_that_is_a_fixture",
        ]
        assert codebase.conftest_violations(planted_conftest) == ["12 helper", "17 Support"]
        assert codebase.support_violations(planted_support) == ["4 helper", "13 fixture_in_support"]


class TestCodeStyle:
    """The code base's style (AGENTS.md, "Code style"), in the package, the tests and the scripts alike: no
    protected-level functions or methods, no function longer than `max_function_lines`, none defined inside another,
    each parameter on a line of its own, each argument of a class constructor and each name of a multi-name import
    too, and no single-character parameter names."""

    def test_no_function_or_method_is_protected(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.protected_definitions(path) == [], path.relative_to(codebase.root)

    def test_no_function_is_longer_than_the_limit(
        self,
        codebase,
        max_function_lines,
    ):
        for path in codebase.code_files():
            assert codebase.long_functions(path, max_function_lines) == [], path.relative_to(codebase.root)

    def test_no_function_is_defined_inside_another(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.nested_functions(path) == [], path.relative_to(codebase.root)

    def test_each_parameter_has_a_line_of_its_own(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.shared_parameter_lines(path) == [], path.relative_to(codebase.root)

    def test_each_argument_of_a_class_constructor_has_a_line_of_its_own(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.shared_argument_lines(path) == [], path.relative_to(codebase.root)

    def test_each_name_of_an_import_of_several_has_a_line_of_its_own(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.shared_import_lines(path) == [], path.relative_to(codebase.root)

    def test_every_constant_is_immutable(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.mutable_constants(path) == [], path.relative_to(codebase.root)

    def test_a_closed_set_of_names_is_an_enumeration_not_a_literal(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.literal_string_sets(path) == [], path.relative_to(codebase.root)

    def test_parameter_names_are_more_than_one_character(
        self,
        codebase,
    ):
        for path in codebase.code_files():
            assert codebase.single_character_parameters(path) == [], path.relative_to(codebase.root)

    def test_the_rules_are_able_to_fail(
        self,
        codebase,
        planted_style,
    ):
        assert codebase.protected_definitions(planted_style) == ["2 _protected", "11 __private"]
        assert codebase.long_functions(planted_style, 3) == ["5 __init__ (4)", "11 __private (4)"]
        assert codebase.nested_functions(planted_style) == ["8 one_line"]
        assert codebase.shared_parameter_lines(planted_style) == ["2 _protected", "8 one_line"]
        assert codebase.shared_argument_lines(planted_style) == ["16 Thing", "21 Path"]
        assert codebase.shared_import_lines(planted_style) == ["24 os"]
        assert codebase.literal_string_sets(planted_style) == [30]
        assert codebase.mutable_constants(planted_style) == ["23 mapping", "32 TABLE"]
        assert codebase.single_character_parameters(planted_style) == [
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
        architecture,
    ):
        layers = codebase.review_rules_layers()
        for folder, expected in architecture.review_rules_layers.items():
            sample = "clean_eval/%s/x.py" % folder
            matched = [
                name for name, globs in layers.items() if any(fnmatch.fnmatch(sample, pattern) for pattern in globs)
            ]
            assert matched == [expected], sample
