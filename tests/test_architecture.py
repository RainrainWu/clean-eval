"""The dependency rule of Clean Architecture, enforced on the imports: domain <- use_cases <- adaptor <- external.
Inner layers know nothing of outer ones and do no I/O, and the domain holds data models only: pydantic models whose
invariants are validators, plus the constants and error types they use. The business rules live in use_cases/rules."""
import ast
import fnmatch
import importlib
import inspect
import re
import unittest
from pathlib import Path

from pydantic import BaseModel

from helpers import ROOT

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
PURE_IMPORTS = {"__future__", "abc", "base64", "collections", "copy", "dataclasses", "datetime", "enum", "fnmatch",
                "functools", "hashlib", "itertools", "json", "math", "re", "typing", "pydantic"}
CLOCK_CALLS = {"now", "today", "utcnow"}  # datetime's clock reads (the time module isn't importable here at all)
MODEL_METHOD_DECORATORS = {"field_validator", "model_validator", "computed_field", "property", "classmethod"}


def imports(path):
    """Absolute module names imported by a file, with relative imports resolved against its package."""
    pkg = [TOP] + list(path.relative_to(PACKAGE).parent.parts)
    found = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = pkg[: len(pkg) - node.level + 1] if node.level else []
            if node.module:
                found.append(".".join(base + [node.module]))
            else:  # `from . import x`: each name is a sibling module
                found += [".".join(base + [a.name]) for a in node.names]
    return found


def layer_files(layer):
    return sorted((PACKAGE / layer).rglob("*.py"))


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

    def test_domain_and_use_cases_never_read_the_clock(self):
        for layer in PURE_ONLY:
            for path in layer_files(layer):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                        self.assertNotIn(node.func.attr, CLOCK_CALLS, "%s:%d" % (path.relative_to(PACKAGE), node.lineno))

    def test_the_launcher_only_reaches_the_external_layer(self):
        names = [n for n in imports(PACKAGE / "__main__.py") if n.startswith(TOP + ".")]
        self.assertEqual(names, ["clean_eval.external.cli"])

    def test_the_rule_is_able_to_fail(self):
        self.assertIn("clean_eval.domain.values", imports(PACKAGE / "use_cases" / "rules" / "scoring.py"))
        self.assertIn("clean_eval.use_cases.rate_repository", imports(PACKAGE / "external" / "wiring.py"))
        self.assertTrue(any(n.startswith("clean_eval.adaptor") for n in imports(PACKAGE / "external" / "cli.py")))
        self.assertIn("os", [n.split(".")[0] for n in imports(PACKAGE / "adaptor" / "gateways" / "files.py")])
        self.assertEqual(data_model_violations("def score(x):\n    return x\nclass M(BaseModel):\n    def rate(self):\n"
                                               "        pass\nprint(1)\n"),
                         ["function score", "method M.rate", "statement on line 6"])


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
