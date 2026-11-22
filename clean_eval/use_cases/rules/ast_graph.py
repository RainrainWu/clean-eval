"""The built-in knowledge graph, from source text alone (stdlib ast), for any revision and with nothing to install.

Links are imports between repository files, resolved by path; functions and methods are nodes; a test file is linked
to a function when it imports the function's file (following one package `__init__` re-export) and names the function.
It cannot see execution flows (`Graph.has_flows` is False), so Business Impact leaves its flow terms out. Per-file
syntax is cached by content, so many revisions of one repository parse each version of a file once."""

import ast
import sys
from collections import defaultdict

from clean_eval.domain.graph import Graph
from clean_eval.domain.source import FileSyntax

STDLIB = frozenset(sys.stdlib_module_names)


def parse_syntax(text):
    """The FileSyntax of a Python file (empty when it doesn't parse)."""
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError, RecursionError:
        return FileSyntax()
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
    return FileSyntax(defs=tuple(defs), imports=tuple(imports), refs=frozenset(refs))


def _dotted(path):
    p = path[:-3]
    if p == "__init__" or p.endswith("/__init__"):
        p = p[: -len("__init__")].rstrip("/")
    return p.replace("/", ".")


def _folder(path):
    return path.rsplit("/", 1)[0] if "/" in path else ""


def module_index(paths):
    """Where an import name can come from in this repository: (dotted name -> path, dotted suffix -> paths)."""
    full, suffix = {}, defaultdict(list)
    for path in paths:
        dotted = _dotted(path)
        if not dotted:
            continue
        full.setdefault(dotted, path)
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            suffix[".".join(parts[i:])].append(path)
    return full, suffix


def _root_ok(candidate, dotted, importer):
    """A suffix match counts when its import root (the folder the dotted name hangs from) is the importer's folder or an
    ancestor of it, or a top-level source folder such as `src` (installed-package layout)."""
    segments = candidate.split("/")
    covered = dotted.count(".") + 1 + (1 if segments[-1] == "__init__.py" else 0)  # `pkg` covers pkg/__init__.py
    root = "/".join(segments[:-covered])
    folder = _folder(importer)
    return root.count("/") == 0 or folder == root or folder.startswith(root + "/")


def lookup(index, dotted, importer):
    full, suffix = index
    if dotted in full:
        return full[dotted]
    cands = [c for c in suffix.get(dotted, ()) if _root_ok(c, dotted, importer)]
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


def resolve(index, importer, module, level, names):
    """The repository files an import of `importer` reaches."""
    full, _ = index
    out = set()
    if level:  # relative imports resolve exactly against the importer's package
        pkg = _folder(importer).split("/") if _folder(importer) else []
        pkg = pkg[: max(len(pkg) - (level - 1), 0)]
        base = ".".join(pkg + (module.split(".") if module else []))
        unresolved = False
        for n in names:
            hit = full.get(base + "." + n if base else n)
            if hit:
                out.add(hit)
            else:
                unresolved = True
        if (unresolved or not names) and base in full:
            out.add(full[base])
        return out
    top = module.split(".")[0]
    if top in STDLIB and top not in full:
        return out
    unresolved = False
    for n in names:
        hit = lookup(index, module + "." + n, importer)
        if hit:
            out.add(hit)
        else:
            unresolved = True
    if unresolved or not names:
        hit = lookup(index, module, importer)
        if hit:
            out.add(hit)
    return out


def builtin_graph(files, cache=None):
    """The Graph of the Python files: imports as links, functions as nodes, and the test files that use them.
    `cache` ({content id: FileSyntax}, e.g. a RatingCache's) is shared across revisions."""
    cache = {} if cache is None else cache

    def syntax_of(f):
        key = f.blob or f.text
        if key not in cache:
            cache[key] = parse_syntax(f.text)
        return cache[key]

    py = [f for f in files if f.path.endswith(".py")]
    syntax = {f.path: syntax_of(f) for f in py}
    index = module_index(syntax)
    nodes, links, tested = {}, defaultdict(set), defaultdict(set)
    for f in py:
        for qual in syntax[f.path].defs:
            nodes["%s::%s" % (f.path, qual)] = ("Function", f.path, f.is_test)
        for module, level, names in syntax[f.path].imports:
            links[f.path].update(t for t in resolve(index, f.path, module, level, names) if t != f.path)
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
    return Graph(nodes=nodes, links=links, tested=dict(tested), flows=[], stats=stats, has_flows=False)
