"""The dependency rule of Clean Architecture, enforced on the imports: domain <- use_cases <- adaptor <- external.
Inner layers know nothing of outer ones and do no I/O. The domain holds data models only: pydantic models whose
invariants are validators, plus the constants and error types they use. The use_cases layer holds the abstract classes
of every external utility the business logic uses (interfaces.py) and module-level functions that take implementations
of them; the business rules are in use_cases/rules. Everything that reaches outside the process does so in the external
layer, each dependency through its one gateway, which implements its interface. Every import is absolute. The tests are
laid out like the package and follow the same dependency rule."""
import ast
import fnmatch
import importlib
import inspect
import re
import unittest
from abc import ABC
from pathlib import Path

from pydantic import BaseModel

from tests.helpers import ROOT

PACKAGE = Path(ROOT) / "clean_eval"
TOP = "clean_eval"
LAYERS = {  # layer -> the layers it may import from
    "domain": {"domain"},
    "use_cases": {"domain", "use_cases"},
    "adaptor": {"domain", "use_cases", "adaptor"},
    "external": {"domain", "use_cases", "adaptor", "external"},
}
PURE_ONLY = {"domain", "use_cases"}
# computation only: no files, network, environment, processes or clock
PURE_IMPORTS = {"__future__", "abc", "ast", "base64", "collections", "copy", "dataclasses", "datetime", "enum", "fnmatch",
                "functools", "hashlib", "itertools", "json", "math", "re", "sys", "typing", "pydantic", "pydantic_core"}
PURE_SYS = {"stdlib_module_names"}  # all the inner layers may take from sys: a constant
CLOCK_CALLS = {"now", "today", "utcnow"}  # datetime's clock reads (the time module isn't importable here at all)
MODEL_METHOD_DECORATORS = {"field_validator", "model_validator", "computed_field", "property", "classmethod"}
# Standard modules that reach outside the process (processes, files, databases, the network, the environment).
REACHING_OUT = {"subprocess", "sqlite3", "os", "shutil", "filecmp", "pathlib", "tempfile", "glob", "io", "socket", "ssl",
                "urllib", "http", "ftplib", "smtplib", "multiprocessing", "ctypes", "webbrowser"}
# Each external dependency has one gateway, the only module allowed what reaches it.
GATEWAYS = {
    "external/git.py": {"subprocess"},                                  # the git CLI
    "external/github.py": {"subprocess"},                               # the gh CLI
    "external/code_review_graph.py": {"subprocess", "sqlite3"},         # the code-review-graph CLI and its database
    "external/filesystem.py": {"os", "shutil", "filecmp", "pathlib"},   # files and folders
    "external/environment.py": {"os"},                                  # environment variables
    "external/clock.py": {"datetime"},                                  # today's date
}
# The interfaces (use_cases/interfaces.py) and the one class implementing each: a gateway or a presenter.
INTERFACES = PACKAGE / "use_cases" / "interfaces.py"
IMPLEMENTATIONS = {
    "Git": "external/git.py", "CodeHost": "external/github.py", "FileSystem": "external/filesystem.py",
    "KnowledgeGraph": "external/code_review_graph.py", "Environment": "external/environment.py", "Clock": "external/clock.py",
    "DiagramPresenter": "adaptor/presenters/diagram.py", "ReportPresenter": "adaptor/presenters/markdown_report.py",
}
DRIVERS = {"external/cli.py", "external/ci.py", "external/agent_guard.py"}  # the console is theirs (they are driven by it)
BOOTSTRAP = {"__main__.py"}  # puts the checkout on sys.path before any of the package can be imported
TESTS = Path(ROOT) / "tests"
TEST_SUPPORT = {"__init__.py", "builders.py", "fakes.py"}  # a layer's test infrastructure, not tests


def imports(path):
    """The module names a file imports (all absolute: relative imports are not allowed, see relative_imports)."""
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            # `from clean_eval import external`: the layer is in the imported name
            found += [node.module + "." + a.name for a in node.names] if node.module == TOP else [node.module]
    return found


def relative_imports(source):
    """The lines of a module's source that import relative to its package (`from . import x`, `from ..a import b`)."""
    return [node.lineno for node in ast.walk(ast.parse(source)) if isinstance(node, ast.ImportFrom) and node.level]


def layer_files(layer):
    return sorted((PACKAGE / layer).rglob("*.py"))


def clock_reads(path):
    """Lines that read the clock: `today()`, `now()` or `utcnow()` called on `date` or `datetime` (the time module
    isn't importable outside the gateways at all)."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in CLOCK_CALLS:
            receiver = node.func.value
            if (receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")) in {"date", "datetime"}:
                yield node.lineno


def sys_uses(path):
    """(name, line) of everything a file takes from sys: `sys.x`, and `from sys import x`."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
            yield node.attr, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            yield from ((a.name, node.lineno) for a in node.names)


def _decorator_name(d):
    d = d.func if isinstance(d, ast.Call) else d
    return d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")


def data_model_violations(source):
    """What, in a module's source, is not a data model: top-level functions, and model methods that are neither
    validators, computed fields, properties nor constructors (an exception's dunder methods aside)."""
    out = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append("function %s" % node.name)
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    named = {_decorator_name(d) for d in item.decorator_list}
                    dunder = item.name.startswith("__") and item.name.endswith("__")
                    if not (named & MODEL_METHOD_DECORATORS or dunder):
                        out.append("method %s.%s" % (node.name, item.name))
        elif not (isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))
                  or isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):  # a docstring
            out.append("statement on line %d" % node.lineno)
    return out


class DependencyRule(unittest.TestCase):
    def test_every_module_belongs_to_a_layer(self):
        top = {p.name for p in PACKAGE.iterdir() if p.is_dir() and p.name != "__pycache__"}
        self.assertEqual(top, set(LAYERS))
        self.assertEqual({p.name for p in PACKAGE.glob("*.py")}, {"__init__.py", "__main__.py"})

    def test_layers_only_import_inward(self):
        for layer, allowed in LAYERS.items():
            for path in layer_files(layer):
                for name in imports(path):
                    parts = name.split(".")
                    if parts[0] == TOP and len(parts) > 1 and parts[1] in LAYERS:
                        self.assertIn(parts[1], allowed, "%s imports %s" % (path.relative_to(PACKAGE), name))

    def test_domain_and_use_cases_do_no_io(self):
        for layer in PURE_ONLY:
            for path in layer_files(layer):
                for name in imports(path):
                    top = name.split(".")[0]
                    if top != TOP:
                        self.assertIn(top, PURE_IMPORTS, "%s imports %s" % (path.relative_to(PACKAGE), name))

    def test_domain_and_use_cases_take_only_constants_from_sys(self):
        for layer in PURE_ONLY:
            for path in layer_files(layer):
                for name, line in sys_uses(path):
                    self.assertIn(name, PURE_SYS, "%s:%d" % (path.relative_to(PACKAGE), line))

    def test_domain_and_use_cases_never_read_the_clock(self):
        for layer in PURE_ONLY:
            for path in layer_files(layer):
                self.assertEqual(list(clock_reads(path)), [], path.relative_to(PACKAGE))

    def test_every_import_is_absolute(self):
        scripts = sorted((Path(ROOT) / ".github" / "scripts").glob("*.py"))
        for path in package_files() + test_files() + scripts:
            self.assertEqual(relative_imports(path.read_text()), [], path.relative_to(ROOT))

    def test_the_launcher_only_reaches_the_external_layer(self):
        names = [n for n in imports(PACKAGE / "__main__.py") if n.startswith(TOP + ".")]
        self.assertEqual(names, ["clean_eval.external.cli"])

    def test_the_rule_is_able_to_fail(self):
        self.assertIn("clean_eval.domain.values", imports(PACKAGE / "use_cases" / "rules" / "scoring.py"))
        self.assertIn("clean_eval.use_cases.rate_repository", imports(PACKAGE / "external" / "cli.py"))
        self.assertEqual(relative_imports("from ..domain import x\nimport os\nfrom . import y\nfrom a import b\n"), [1, 3])
        self.assertEqual([n for n, _ in sys_uses(PACKAGE / "external" / "agent_guard.py")], ["stdin", "stderr"])
        self.assertTrue(any(n.startswith("clean_eval.adaptor") for n in imports(PACKAGE / "external" / "cli.py")))
        self.assertIn("os", [n.split(".")[0] for n in imports(PACKAGE / "external" / "filesystem.py")])
        self.assertEqual(data_model_violations("def score(x):\n    return x\nclass M(BaseModel):\n    def rate(self):\n"
                                               "        pass\nprint(1)\n"),
                         ["function score", "method M.rate", "statement on line 6"])


def package_files():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def calls(path):
    """(callee, line) of every call in a file: `open` for a bare name, `today` for `x.today()`."""
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call):
            f = node.func
            yield (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")), node.lineno


class ExternalDependenciesStayInTheirGateways(unittest.TestCase):
    def test_only_a_gateway_imports_what_reaches_outside_the_process(self):
        for path in package_files():
            rel = path.relative_to(PACKAGE).as_posix()
            if rel in BOOTSTRAP:
                continue
            reaching = {n.split(".")[0] for n in imports(path)} & (REACHING_OUT | {"datetime"} if rel.startswith("external/")
                                                                    else REACHING_OUT)
            self.assertLessEqual(reaching, GATEWAYS.get(rel, set()), rel)

    def test_each_gateway_is_one_gateway_class(self):
        for rel in GATEWAYS:
            tree = ast.parse((PACKAGE / rel).read_text())
            classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef) and not n.name.startswith("_")]
            self.assertEqual(len(classes), 1, rel)
            self.assertTrue(classes[0].endswith("Gateway"), rel)

    def test_files_the_clock_and_the_environment_are_reached_only_through_their_gateways(self):
        for path in package_files():
            rel = path.relative_to(PACKAGE).as_posix()
            for callee, line in calls(path):
                if callee == "open":
                    self.assertEqual(rel, "external/filesystem.py", "%s:%d opens a file" % (rel, line))
            if rel != "external/clock.py":
                self.assertEqual(list(clock_reads(path)), [], "%s reads the clock" % rel)
            if rel != "external/environment.py":
                for node in ast.walk(ast.parse(path.read_text())):
                    self.assertFalse(isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv", "putenv"},
                                     "%s:%d" % (rel, getattr(node, "lineno", 0)))

    def test_only_the_drivers_use_the_console(self):
        for path in package_files():
            rel = path.relative_to(PACKAGE).as_posix()
            if rel in DRIVERS:
                continue
            for callee, line in calls(path):
                self.assertNotIn(callee, {"print", "input"}, "%s:%d" % (rel, line))
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
                    self.assertNotIn(node.attr, {"stdin", "stdout", "stderr", "exit", "argv"}, "%s:%d" % (rel, node.lineno))

    def test_the_rule_is_able_to_fail(self):
        self.assertIn("subprocess", {n.split(".")[0] for n in imports(PACKAGE / "external" / "git.py")})
        self.assertTrue(list(clock_reads(PACKAGE / "external" / "clock.py")))  # the one clock read is found
        self.assertIn("open", {callee for callee, _ in calls(PACKAGE / "external" / "filesystem.py")})


def public_methods(cls):
    return {name for name, value in vars(cls).items() if callable(value) and not name.startswith("_")}


class UseCasesHoldInterfacesAndFunctions(unittest.TestCase):
    """The use_cases layer holds two things: the abstract classes of every external utility the business logic uses
    (interfaces.py), implemented by the outer layers, and module-level functions that take those implementations as
    parameters and call only the abstract methods."""

    def interfaces(self):
        module = importlib.import_module("clean_eval.use_cases.interfaces")
        return {name: cls for name, cls in inspect.getmembers(module, inspect.isclass) if cls.__module__ == module.__name__}

    def test_the_interfaces_are_abstract_classes_whose_methods_are_all_abstract(self):
        tree = ast.parse(INTERFACES.read_text())
        self.assertEqual([n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))], [])
        interfaces = self.interfaces()
        self.assertEqual(set(interfaces), set(IMPLEMENTATIONS))
        for name, cls in interfaces.items():
            self.assertTrue(issubclass(cls, ABC), name)
            self.assertEqual(public_methods(cls), set(cls.__abstractmethods__), name)

    def test_the_rest_of_the_layer_is_module_level_functions(self):
        for path in layer_files("use_cases"):
            if path != INTERFACES:
                classes = [n.name for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, ast.ClassDef)]
                self.assertEqual(classes, [], path.relative_to(PACKAGE))

    def test_each_interface_has_one_implementation_with_nothing_more(self):
        for name, interface in self.interfaces().items():
            rel = IMPLEMENTATIONS[name]
            module = importlib.import_module("clean_eval." + rel[: -len(".py")].replace("/", "."))
            classes = [cls for _, cls in inspect.getmembers(module, inspect.isclass)
                       if cls.__module__ == module.__name__ and issubclass(cls, interface)]
            self.assertEqual(len(classes), 1, rel)
            self.assertFalse(inspect.isabstract(classes[0]), rel)  # every abstract method implemented
            # and nothing the interface doesn't declare: what the business logic can use is what it defined
            self.assertLessEqual(public_methods(classes[0]), set(interface.__abstractmethods__), rel)

    def test_every_gateway_implements_an_interface(self):
        self.assertLessEqual(set(GATEWAYS), set(IMPLEMENTATIONS.values()))


class DomainHoldsDataModels(unittest.TestCase):
    def domain_modules(self):
        for path in layer_files("domain"):
            if path.name != "__init__.py":
                yield path, importlib.import_module("clean_eval.domain." + path.stem)

    def test_no_functions_and_no_logic_methods(self):
        for path, _ in self.domain_modules():
            self.assertEqual(data_model_violations(path.read_text()), [], path.name)

    def test_every_class_is_a_pydantic_model_or_an_error(self):
        seen = 0
        for path, module in self.domain_modules():
            for name, cls in inspect.getmembers(module, inspect.isclass):
                if cls.__module__ == module.__name__:
                    seen += 1
                    self.assertTrue(issubclass(cls, (BaseModel, Exception)), "%s.%s" % (path.name, name))
        self.assertGreater(seen, 30)


def test_files():
    return sorted(p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts)


def layer_tests():
    """(layer, path) of every module under tests/<layer>/."""
    return [(layer, path) for layer in LAYERS for path in test_files() if path.is_relative_to(TESTS / layer)]


def tested_module(path):
    """The module tests/<layer>/.../test_<name>.py is about: clean_eval/<layer>/.../<name>.py."""
    return PACKAGE / path.relative_to(TESTS).parent / path.name[len("test_"):]


class TestsMirrorThePackage(unittest.TestCase):
    """tests/<layer>/ holds the tests of clean_eval/<layer>/, test_<module>.py for <module>.py (plus the layer's test
    support: builders.py, fakes.py), and follows the dependency rule: a layer's tests import only that layer and the inner
    ones, from the package and from the tests alike. Tests that span the layers sit at the top of tests/."""

    def test_each_test_module_names_the_module_it_tests(self):
        for _, path in layer_tests():
            if path.name not in TEST_SUPPORT:
                self.assertTrue(path.name.startswith("test_") and tested_module(path).is_file(),
                                "%s names no module of the package" % path.relative_to(ROOT))

    def test_every_test_folder_is_a_package(self):  # unittest discovers tests in packages only
        for folder in {path.parent for path in test_files()}:
            self.assertTrue((folder / "__init__.py").is_file(), folder.relative_to(ROOT))

    def test_a_layers_tests_import_only_that_layer_and_inner_ones(self):
        for layer, path in layer_tests():
            for name in imports(path):
                parts = name.split(".")
                if parts[0] in (TOP, "tests") and len(parts) > 1 and parts[1] in LAYERS:
                    self.assertIn(parts[1], LAYERS[layer], "%s imports %s" % (path.relative_to(ROOT), name))

    def test_the_rule_is_able_to_fail(self):
        self.assertFalse(tested_module(TESTS / "use_cases" / "test_nothing.py").is_file())
        self.assertLessEqual({"tests.use_cases.fakes", "clean_eval.external.git"},
                             set(imports(TESTS / "external" / "test_git.py")))
        self.assertIn(("external", TESTS / "external" / "test_git.py"), layer_tests())


class ReviewRulesCoverThisPackage(unittest.TestCase):
    """The default review rules must place every clean_eval layer in the matching rules layer, so the dependency rules
    (CA-01, CA-02) apply to this repository's own code in reviews."""
    EXPECTED = {"domain": "domain", "use_cases": "application", "use_cases/rules": "application", "adaptor": "interface",
                "external": "infrastructure"}

    def rules_layers(self):
        text = (Path(ROOT) / ".claude" / "skills" / "pr-review" / "review-rules.md").read_text()
        section = text.split("## Layers", 1)[1].split("\n## ", 1)[0]
        rows = [l for l in section.splitlines() if l.startswith("| ") and "`" in l]
        return {row.split("|")[1].strip(): re.findall(r"`([^`]+)`", row.split("|")[2]) for row in rows}

    def test_each_package_layer_maps_to_its_rules_layer(self):
        layers = self.rules_layers()
        for folder, expected in self.EXPECTED.items():
            sample = "clean_eval/%s/x.py" % folder
            matched = [name for name, globs in layers.items() if any(fnmatch.fnmatch(sample, g) for g in globs)]
            self.assertEqual(matched, [expected], sample)


if __name__ == "__main__":
    unittest.main()
