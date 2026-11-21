"""The dependency rule of Clean Architecture, enforced on the imports: domain <- use_cases <- adaptor <- external.
Inner layers know nothing of outer ones, and the two inner layers do no I/O."""
import ast
import unittest
from pathlib import Path

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
PURE_STDLIB = {"__future__", "abc", "collections", "copy", "dataclasses", "enum", "fnmatch", "functools", "hashlib",
               "itertools", "json", "math", "re", "typing"}


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
                        self.assertIn(top, PURE_STDLIB, "%s imports %s" % (path.relative_to(PACKAGE), name))

    def test_the_launcher_only_reaches_the_external_layer(self):
        names = [n for n in imports(PACKAGE / "__main__.py") if n.startswith(TOP + ".")]
        self.assertEqual(names, ["clean_eval.external.cli"])

    def test_the_rule_is_able_to_fail(self):
        self.assertIn("clean_eval.domain.policy", imports(PACKAGE / "domain" / "scoring.py"))
        self.assertIn("clean_eval.use_cases.rate_repository", imports(PACKAGE / "external" / "wiring.py"))
        self.assertTrue(any(n.startswith("clean_eval.adaptor") for n in imports(PACKAGE / "external" / "cli.py")))
        self.assertIn("os", [n.split(".")[0] for n in imports(PACKAGE / "adaptor" / "gateways" / "files.py")])


if __name__ == "__main__":
    unittest.main()
