"""The architecture analysis behind tests/test_architecture.py: a checkout of clean-eval, read as code. Classes and
constants only; the `codebase` fixture in tests/conftest.py hands it out.

A rule asks the analysis what the code holds (a module's imports, calls and definitions; the files of the package and
of its tests) and asserts on the answer. A check that is more than a question is a method that returns what it finds,
empty when the rule holds (`data_model_violations`, `unittest_beyond_mock`). Every method that reads a module takes it
as a Path or as source text, so an able-to-fail check can plant the code a rule must reject."""

import ast
import importlib
import inspect
import re
from pathlib import Path

CLOCK_CALLS = frozenset(
    {"now", "today", "utcnow"}
)  # datetime's clock reads (the time module isn't importable here at all)
MODEL_METHOD_DECORATORS = frozenset({"field_validator", "model_validator", "computed_field", "property", "classmethod"})
REVIEW_RULES = (
    Path(
        ".claude",
    )
    / "skills"
    / "pr-review"
    / "references"
    / "rules"
    / "policy.md"
)  # the default review rules' policy, which maps paths to layers


class Codebase:
    """The checkout at `root`, read as code: the files of its package `top` and of its tests, what a module holds, the
    package's classes (imported: only this checkout's package is importable) and its default review rules."""

    def __init__(
        self,
        root,
        top="clean_eval",
    ):
        self.root = Path(
            root,
        )
        self.top = top
        self.package = self.root / top
        self.tests = self.root / "tests"

    # The files.

    def package_files(
        self,
    ):
        """Every Python file of the package, sorted."""
        return sorted(path for path in self.package.rglob("*.py") if "__pycache__" not in path.parts)

    def layer_files(
        self,
        layer,
    ):
        """The Python files of one layer of the package (clean_eval/<layer>/), sorted."""
        return sorted((self.package / layer).rglob("*.py"))

    def all_test_files(
        self,
    ):
        """Every Python file of the tests (tests/), sorted: the test modules and their support."""
        return sorted(path for path in self.tests.rglob("*.py") if "__pycache__" not in path.parts)

    def layer_tests(
        self,
        layers,
    ):
        """(layer, path) of every Python file under tests/<layer>/, for each of `layers` in turn."""
        return [
            (layer, path)
            for layer in layers
            for path in self.all_test_files()
            if path.is_relative_to(self.tests / layer)
        ]

    def module_under_test(
        self,
        path,
    ):
        """The module the test module tests/<layer>/.../test_<name>.py is about: clean_eval/<layer>/.../<name>.py."""
        return self.package / path.relative_to(self.tests).parent / path.name[len("test_") :]

    # What a module holds: `module` is a Path, read from disk, or source text.

    def source(
        self,
        module,
    ):
        """The source text of `module`."""
        return module.read_text() if isinstance(module, Path) else module

    def parse(
        self,
        module,
    ):
        """The syntax tree of `module`."""
        return ast.parse(self.source(module))

    def nodes(
        self,
        module,
        *kinds,
    ):
        """The nodes of `module`'s syntax tree that are any of `kinds` (classes of ast), in ast.walk's order."""
        return [node for node in ast.walk(self.parse(module)) if isinstance(node, kinds)]

    def imports(
        self,
        module,
    ):
        """The names of the modules `module` imports. All absolute: relative imports are not allowed (see
        relative_imports). `from clean_eval import external` gives clean_eval.external: the layer is in the name."""
        found = []
        for node in self.nodes(module, ast.Import, ast.ImportFrom):
            if isinstance(node, ast.Import):
                found += [alias.name for alias in node.names]
            elif not node.level and node.module is not None:
                found += (
                    [node.module + "." + alias.name for alias in node.names]
                    if node.module == self.top
                    else [node.module]
                )
        return found

    def imported_names(
        self,
        module,
    ):
        """Everything `module` imports (absolutely), dotted: `import a.b` gives a.b; `from a import b, c` gives a.b and
        a.c."""
        found = []
        for node in self.nodes(module, ast.Import, ast.ImportFrom):
            if isinstance(node, ast.Import):
                found += [alias.name for alias in node.names]
            elif not node.level and node.module is not None:
                found += [node.module + "." + alias.name for alias in node.names]
        return found

    def settings_built(
        self,
        module,
    ):
        """The lines where `module` builds a Settings, which reads the environment: a call of Settings itself, or of one
        of its class methods that make one (model_validate, model_validate_json, model_construct)."""
        makers = {"model_validate", "model_validate_json", "model_construct"}
        found = []
        for node in self.nodes(module, ast.Call):
            callee = node.func
            if isinstance(callee, ast.Name) and callee.id == "Settings":
                found.append(node.lineno)
            elif isinstance(callee, ast.Attribute) and callee.attr in makers:
                if isinstance(callee.value, ast.Name) and callee.value.id == "Settings":
                    found.append(node.lineno)
        return sorted(found)

    def unittest_beyond_mock(
        self,
        module,
    ):
        """What `module` takes from unittest besides unittest.mock: `unittest` for `import unittest`,
        `unittest.TestCase` for `from unittest import TestCase`."""
        return [
            name
            for name in self.imported_names(module)
            if name.split(".")[0] == "unittest" and not (name + ".").startswith("unittest.mock.")
        ]

    def relative_imports(
        self,
        module,
    ):
        """The lines of `module` that import relative to its package (`from . import x`, `from ..a import b`)."""
        return [node.lineno for node in self.nodes(module, ast.ImportFrom) if node.level]

    def calls(
        self,
        module,
    ):
        """(callee, line) of every call in `module`: `open` for a bare name, `today` for `x.today()`."""
        return [
            (node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", ""), node.lineno)
            for node in self.nodes(module, ast.Call)
        ]

    def clock_reads(
        self,
        module,
    ):
        """The lines of `module` that read the clock: `today()`, `now()` or `utcnow()` called on `date` or `datetime`
        (the time module isn't importable outside the gateways at all)."""
        lines = []
        for node in self.nodes(module, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr in CLOCK_CALLS:
                receiver = node.func.value
                name = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
                if name in {"date", "datetime"}:
                    lines.append(node.lineno)
        return lines

    def sys_uses(
        self,
        module,
    ):
        """(name, line) of everything `module` takes from sys: `sys.x`, and `from sys import x`."""
        found = []
        for node in self.nodes(module, ast.Attribute, ast.ImportFrom):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sys":
                found.append((node.attr, node.lineno))
            elif isinstance(node, ast.ImportFrom) and node.module == "sys":
                found += [(alias.name, node.lineno) for alias in node.names]
        return found

    def decorator_name(
        self,
        decorator,
    ):
        """The name of a decorator, called or not: `field_validator` for `@field_validator("x")`."""
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        return target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")

    def data_model_violations(
        self,
        module,
    ):
        """What, in `module`, is not a data model: top-level functions, and model methods that are neither validators,
        computed fields, properties nor constructors (an exception's dunder methods aside)."""
        found = []
        for node in self.parse(module).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append("function %s" % node.name)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        named = {self.decorator_name(decorator) for decorator in item.decorator_list}
                        dunder = item.name.startswith("__") and item.name.endswith("__")
                        if not (named & MODEL_METHOD_DECORATORS or dunder):
                            found.append("method %s.%s" % (node.name, item.name))
            elif not (
                isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.AnnAssign))
                or isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
            ):  # a docstring
                found.append("statement on line %d" % node.lineno)
        return found

    # The code style, in the package, the tests and the scripts: no protected-level functions or methods, each
    # parameter on a line of its own, no single-character parameter names (AGENTS.md, "Code style").

    def code_files(
        self,
    ):
        """Every Python file of the code base, sorted: the package's, the tests' and the scripts' in .github/."""
        scripts = sorted(path for path in (self.root / ".github").rglob("*.py") if "__pycache__" not in path.parts)
        return self.package_files() + self.all_test_files() + scripts

    def functions(
        self,
        module,
    ):
        """Every function of `module`: module-level functions, methods and nested functions (lambdas aside)."""
        return self.nodes(module, ast.FunctionDef, ast.AsyncFunctionDef)

    def parameters(
        self,
        function,
    ):
        """The parameters of a function or lambda node, in order: positional, *args, keyword-only, **kwargs."""
        arguments = function.args
        return [
            *arguments.posonlyargs,
            *arguments.args,
            *([arguments.vararg] if arguments.vararg else []),
            *arguments.kwonlyargs,
            *([arguments.kwarg] if arguments.kwarg else []),
        ]

    def protected_definitions(
        self,
        module,
    ):
        """`line name` of every function or method of `module` whose name starts with an underscore (dunders aside)."""
        return [
            "%d %s" % (function.lineno, function.name)
            for function in self.functions(module)
            if function.name.startswith("_") and not (function.name.startswith("__") and function.name.endswith("__"))
        ]

    def shared_parameter_lines(
        self,
        module,
    ):
        """`line name` of every function of `module` with a parameter on its `def` line or two parameters on one line.
        Lambdas can't span lines, so they are left out."""
        found = []
        for function in self.functions(module):
            lines = [parameter.lineno for parameter in self.parameters(function)]
            if lines and (min(lines) == function.lineno or len(set(lines)) != len(lines)):
                found.append("%d %s" % (function.lineno, function.name))
        return found

    def shared_argument_lines(
        self,
        module,
    ):
        """`line name` of every class-constructor call of `module` with an argument on the call's line or two arguments
        on one line. A constructor is a callee named in CapWords, as PEP 8 names classes (`RunFolders`, `Path`,
        `HeatmapError`, pydantic's `Field`), not a builtin's lowercase name (`dict`, `str`)."""
        found = []
        for call in self.nodes(module, ast.Call):
            name = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            lines = [argument.lineno for argument in [*call.args, *call.keywords]]
            constructor = name[:1].isupper() and not name.isupper()
            if constructor and lines and (min(lines) == call.lineno or len(set(lines)) != len(lines)):
                found.append("%d %s" % (call.lineno, name))
        return sorted(found, key=lambda entry: int(entry.split()[0]))

    def long_functions(
        self,
        module,
        limit,
    ):
        """`line name (lines)` of every function of `module` that takes more than `limit` lines, from its `def` to its
        last line, its signature and docstring included."""
        found = []
        for function in self.functions(module):
            size = function.end_lineno - function.lineno + 1
            if size > limit:
                found.append("%d %s (%d)" % (function.lineno, function.name, size))
        return found

    def nested_functions(
        self,
        module,
    ):
        """`line name` of every function of `module` defined inside another function: a helper is a module-level
        function, or a method. (Lambdas are expressions, not definitions, and are left out.)"""
        found = []
        for function in self.functions(module):
            for statement in function.body:
                for node in ast.walk(statement):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        found.append((node.lineno, node.name))
        return ["%d %s" % entry for entry in sorted(set(found))]

    def shared_import_lines(
        self,
        module,
    ):
        """`line module` of every import of several names in `module` with a name on the import's line, or two names
        on one line."""
        found = []
        for node in self.nodes(module, ast.ImportFrom):
            lines = [alias.lineno for alias in node.names]
            if len(lines) > 1 and (min(lines) == node.lineno or len(set(lines)) != len(lines)):
                found.append("%d %s" % (node.lineno, node.module))
        return found

    def module_level_names(
        self,
        module,
    ):
        """The names `module` binds at its top level by assignment or as a `type` alias: its constants and type
        aliases (its functions and classes aside)."""
        names = []
        for node in self.parse(module).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    elements = target.elts if isinstance(target, ast.Tuple) else [target]
                    names += [element.id for element in elements if isinstance(element, ast.Name)]
            elif isinstance(node, ast.TypeAlias):
                names.append(node.name.id)
        return names

    def shared_definitions(
        self,
        modules,
    ):
        """{name: the positions in `modules` of the modules that bind it} of every top-level name that more than one
        of `modules` binds."""
        found = {}
        for position, module in enumerate(modules):
            for name in sorted(set(self.module_level_names(module))):
                found.setdefault(name, []).append(position)
        return {name: positions for name, positions in found.items() if len(positions) > 1}

    def mutable_constants(
        self,
        module,
    ):
        """`line name` of every value at the top of `module` bound to a list, set or dict (a display, a comprehension,
        or a list(), set() or dict() call), which any importer could change: a constant is a tuple, a frozenset or a
        frozendict. (`pytestmark` aside.)"""
        mutable = (ast.List, ast.Set, ast.Dict, ast.ListComp, ast.SetComp, ast.DictComp)
        found = []
        for node in self.parse(module).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                value = node.value
                called = isinstance(value, ast.Call) and getattr(value.func, "id", "") in ("list", "set", "dict")
                if getattr(target, "id", "pytestmark") != "pytestmark" and (isinstance(value, mutable) or called):
                    found.append("%d %s" % (node.lineno, target.id))
        return found

    def literal_string_sets(
        self,
        module,
    ):
        """The lines of `module` that spell a closed set of names as a `Literal` of two or more strings, where a StrEnum
        belongs (a Literal of one value, or of booleans, is no set of names)."""
        found = []
        for node in self.nodes(module, ast.Subscript):
            if isinstance(node.value, ast.Name) and node.value.id == "Literal":
                values = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
                strings = [
                    value for value in values if isinstance(value, ast.Constant) and isinstance(value.value, str)
                ]
                if len(strings) > 1:
                    found.append(node.lineno)
        return sorted(found)

    def single_character_parameters(
        self,
        module,
    ):
        """`line function(parameter)` of every one-character parameter of a function or lambda of `module`."""
        return [
            "%d %s(%s)" % (function.lineno, getattr(function, "name", "lambda"), parameter.arg)
            for function in self.nodes(module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            for parameter in self.parameters(function)
            if len(parameter.arg) == 1
        ]

    # The shape of the tests (tests/AGENTS.md, "Writing tests").

    def test_modules(
        self,
    ):
        """The test modules: tests/**/test_*.py."""
        return [path for path in self.all_test_files() if path.name.startswith("test_")]

    def conftests(
        self,
    ):
        """Every conftest.py of the tests."""
        return [path for path in self.all_test_files() if path.name == "conftest.py"]

    def support_modules(
        self,
    ):
        """The tests' support modules: the files that are neither test modules, conftest.py nor __init__.py."""
        return [
            path
            for path in self.all_test_files()
            if not path.name.startswith("test_") and path.name not in ("conftest.py", "__init__.py")
        ]

    def is_fixture(
        self,
        function,
    ):
        """Whether a function node is a pytest fixture (`@pytest.fixture`, called or not)."""
        return any(self.decorator_name(decorator) == "fixture" for decorator in function.decorator_list)

    def is_docstring(
        self,
        node,
        position,
    ):
        """Whether `node`, at `position` in its body, is that body's docstring."""
        return position == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)

    def constant_names(
        self,
        node,
    ):
        """The names an assignment binds, or None when it binds anything but plain names (`a.b = 1`, `a[0] = 1`)."""
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = []
        for target in targets:
            for element in target.elts if isinstance(target, ast.Tuple) else [target]:
                if not isinstance(element, ast.Name):
                    return None
                names.append(element.id)
        return names

    def test_module_violations(
        self,
        module,
    ):
        """`line name` of what, in a test module, is not a test. Its top level holds only imports, Test* classes and
        `pytestmark`: no value (its static assets are fixtures, in a conftest.py), and a Test* class only its docstring
        and test methods (no helper, no fixture)."""
        found = []
        for position, node in enumerate(self.parse(module).body):
            if self.is_docstring(node, position) or isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                names = self.constant_names(node)
                if names == ["pytestmark"]:
                    continue
                found.append("%d %s" % (node.lineno, ", ".join(names or ["assignment"])))
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                for index, item in enumerate(node.body):
                    is_test = isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test")
                    if not (self.is_docstring(item, index) or is_test and not self.is_fixture(item)):
                        found.append("%d %s.%s" % (item.lineno, node.name, getattr(item, "name", type(item).__name__)))
            else:
                found.append("%d %s" % (node.lineno, getattr(node, "name", type(node).__name__)))
        return found

    def conftest_violations(
        self,
        module,
    ):
        """`line name` of what, in a conftest.py, is neither a fixture, a pytest hook (`pytest_*`) nor a constant: other
        functions, and classes (support classes belong in support modules)."""
        return [
            "%d %s" % (node.lineno, node.name)
            for node in self.parse(module).body
            if isinstance(node, ast.ClassDef)
            or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not (self.is_fixture(node) or node.name.startswith("pytest_"))
        ]

    def support_violations(
        self,
        module,
    ):
        """`line name` of the functions at the top level of a support module, and of any fixture in it: support holds
        classes and constants only, and fixtures live in conftest.py."""
        top = [node for node in self.parse(module).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        fixtures = [function for function in self.functions(module) if self.is_fixture(function)]
        found = {(function.lineno, function.name) for function in top + fixtures}
        return ["%d %s" % (line, name) for line, name in sorted(found)]

    # The package's classes, imported.

    def module_name(
        self,
        path,
    ):
        """The dotted name of the package's module at `path`: clean_eval.external.git for clean_eval/external/git.py."""
        return ".".join(path.relative_to(self.root).with_suffix("").parts)

    def classes(
        self,
        path,
    ):
        """{name: class} of the classes the package's module at `path` defines (not those it imports)."""
        module = importlib.import_module(self.module_name(path))
        return {
            name: value
            for name, value in inspect.getmembers(module, inspect.isclass)
            if value.__module__ == module.__name__
        }

    def public_methods(
        self,
        cls,
    ):
        """The names of the public methods class `cls` defines itself."""
        return {name for name, value in vars(cls).items() if callable(value) and not name.startswith("_")}

    # The review rules.

    def review_rules_layers(
        self,
    ):
        """{rules layer: [path globs]}, from the Layers table of the default review rules."""
        text = (self.root / REVIEW_RULES).read_text()
        section = text.split("## Layers\n", 1)[1].split("\n## ", 1)[0]
        rows = [line for line in section.splitlines() if line.startswith("| ") and "`" in line]
        return {row.split("|")[1].strip(): re.findall(r"`([^`]+)`", row.split("|")[2]) for row in rows}
