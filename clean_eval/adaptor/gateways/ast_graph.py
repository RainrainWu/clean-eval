"""GraphProvider from source text alone (stdlib ast), for any revision and with nothing to install.

Links are imports between repository files, resolved by path; functions and methods are nodes; a test file is linked
to a function when it imports the function's file (following one package `__init__` re-export) and names the function.
It cannot see execution flows (`Graph.has_flows` is False), so Business Impact leaves its flow terms out. Per-file
results are cached by content, so many revisions of one repository parse each version of a file once."""
import ast
import sys
from collections import defaultdict
from dataclasses import dataclass

from ...domain.graph import Graph

STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))


@dataclass(frozen=True)
class _Syntax:
    defs: tuple       # qualified names of functions and methods: "f", "Class.method"
    imports: tuple    # (module, level, names)
    refs: frozenset   # identifiers, attribute names and imported names used in the file


def parse_syntax(text):
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return _Syntax((), (), frozenset())
    defs, imports, refs = [], [], set()

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.append(prefix + child.name)
                visit(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.append((a.name, 0, ()))
                refs.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            names = tuple(a.name for a in node.names if a.name != "*")
            imports.append((node.module or "", node.level or 0, names))
            refs.update(names)
            refs.update(a.asname for a in node.names if a.asname)
        elif isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
    return _Syntax(tuple(defs), tuple(imports), frozenset(refs))


def _dotted(path):
    p = path[:-3]
    if p == "__init__" or p.endswith("/__init__"):
        p = p[: -len("__init__")].rstrip("/")
    return p.replace("/", ".")


def _folder(path):
    return path.rsplit("/", 1)[0] if "/" in path else ""


class _ModuleIndex:
    """Where an import name can come from in this repository."""

    def __init__(self, paths):
        self.full, self.suffix = {}, defaultdict(list)
        for path in paths:
            dotted = _dotted(path)
            if not dotted:
                continue
            self.full.setdefault(dotted, path)
            parts = dotted.split(".")
            for i in range(1, len(parts)):
                self.suffix[".".join(parts[i:])].append(path)

    @staticmethod
    def _root_ok(candidate, dotted, importer):
        """A suffix match counts when its import root (the folder the dotted name hangs from) is the importer's folder or
        an ancestor of it, or a top-level source folder such as `src` (installed-package layout)."""
        segments = candidate.split("/")
        covered = dotted.count(".") + 1 + (1 if segments[-1] == "__init__.py" else 0)  # `pkg` covers pkg/__init__.py
        root = "/".join(segments[:-covered])
        folder = _folder(importer)
        return root.count("/") == 0 or folder == root or folder.startswith(root + "/")

    def lookup(self, dotted, importer):
        if dotted in self.full:
            return self.full[dotted]
        cands = [c for c in self.suffix.get(dotted, ()) if self._root_ok(c, dotted, importer)]
        if not cands:
            return None
        imp = importer.split("/")

        def shared(c):
            n = 0
            for a, b in zip(imp, c.split("/")):
                if a != b:
                    break
                n += 1
            return n
        return max(cands, key=lambda c: (shared(c), -len(c)))

    def resolve(self, importer, module, level, names):
        out = set()
        if level:  # relative imports resolve exactly against the importer's package
            pkg = _folder(importer).split("/") if _folder(importer) else []
            pkg = pkg[: max(len(pkg) - (level - 1), 0)]
            base = ".".join(pkg + (module.split(".") if module else []))
            unresolved = False
            for n in names:
                hit = self.full.get(base + "." + n if base else n)
                if hit:
                    out.add(hit)
                else:
                    unresolved = True
            if (unresolved or not names) and base in self.full:
                out.add(self.full[base])
            return out
        top = module.split(".")[0]
        if top in STDLIB and top not in self.full:
            return out
        unresolved = False
        for n in names:
            hit = self.lookup(module + "." + n, importer)
            if hit:
                out.add(hit)
            else:
                unresolved = True
        if unresolved or not names:
            hit = self.lookup(module, importer)
            if hit:
                out.add(hit)
        return out


class BuiltinGraph:
    def __init__(self):
        self._cache = {}

    def _syntax(self, f):
        key = f.blob or f.text
        hit = self._cache.get(key)
        if hit is None:
            hit = self._cache[key] = parse_syntax(f.text)
        return hit

    def graph_for(self, snapshot, files):
        py = [f for f in files if f.path.endswith(".py")]
        syntax = {f.path: self._syntax(f) for f in py}
        index = _ModuleIndex(syntax)
        nodes, links, tested = {}, defaultdict(set), defaultdict(set)
        for f in py:
            for qual in syntax[f.path].defs:
                nodes["%s::%s" % (f.path, qual)] = ("Function", f.path, f.is_test)
            for module, level, names in syntax[f.path].imports:
                links[f.path].update(t for t in index.resolve(f.path, module, level, names) if t != f.path)
        for t in (f for f in py if f.is_test):
            targets = set(links.get(t.path, ()))
            for tgt in list(targets):
                if tgt.endswith("__init__.py"):  # one hop through a package's re-exports
                    targets |= links.get(tgt, set())
            refs = syntax[t.path].refs
            for tgt in targets:
                for qual in syntax[tgt].defs:
                    if qual.split(".")[-1] in refs:
                        tested["%s::%s" % (tgt, qual)].add(t.path)
        links = {k: v for k, v in links.items() if v}
        stats = {"source": "built-in ast", "nodes": len(nodes), "edges": sum(len(v) for v in links.values()), "flows": 0}
        return Graph(nodes, links, dict(tested), [], stats, has_flows=False)
