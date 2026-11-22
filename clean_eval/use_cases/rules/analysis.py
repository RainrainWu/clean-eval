"""Reading code, from its text alone: classifying paths, splitting a repository into modules, queries over a
knowledge graph, what one Python file says about risk, and the built-in knowledge graph."""

import ast
import fnmatch
import functools
import re
import sys
from collections import defaultdict
from collections.abc import Container, Iterable
from typing import Optional

from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import FileSignals, FileSyntax, Graph, PyStats, SourceFile

# Rules that classify paths and names: tests, test scales, manifests, tooling, core-flow names, ignored paths.


@functools.lru_cache(maxsize=128)
def compiled(
    pattern: str,
) -> re.Pattern[str]:
    return re.compile(pattern)


def is_test(
    path: str,
    policy: Policy,
) -> bool:
    parts = path.split("/")
    return any(x in policy.test_dirs for x in parts[:-1]) or bool(compiled(policy.test_file).match(parts[-1]))


def classify_scale(
    path: str,
) -> str:
    p = path.lower()
    if "e2e" in p or "end_to_end" in p:
        return "e2e"
    if "integration" in p or "live" in p or "smoke" in p:
        return "integration"
    if "unit" in p or "/tests/" in "/" + p or "/test/" in "/" + p:
        return "unit"
    return "other"


def not_applicable(
    module_name: str,
    policy: Policy,
) -> Optional[str]:
    """Reason a module is not rated for Unverified (manifest or test code), else None."""
    for reason, pattern in policy.not_applicable:
        if compiled(pattern).search(module_name.lower()):
            return reason
    return None


def has_core_keyword(
    module_name: str,
    policy: Policy,
) -> bool:
    return bool(compiled(policy.keywords).search(module_name.lower()))


def looks_like_tooling(
    module_name: str,
    first_path: str,
    policy: Policy,
) -> bool:
    rx = compiled(policy.tooling)
    return bool(rx.search(module_name.lower()) or rx.search(first_path.lower()))


def is_migration(
    path: str,
    policy: Policy,
) -> bool:
    return bool(compiled(policy.migration_path).search(path))


def excluded(
    path: str,
    policy: Policy,
) -> bool:
    """Source the policy leaves out of the ratings (vendored or generated code)."""
    return any(fnmatch.fnmatch(path, g) for g in policy.exclude)


def ignored(
    path: str,
    policy: Policy,
) -> bool:
    """A changed path that carries no risk of its own (documentation, images)."""
    return any(fnmatch.fnmatch(path, g) for g in policy.ignore_paths)


# Group source files into sub-modules (top-level folders, split level by level down to a depth), and find the module a
# path belongs to.

ROOT_MODULE = "(root)"
FILES_SUFFIX = "/(files)"


def descend_single_chains(
    name: str,
    files: list[SourceFile],
) -> tuple[str, int, set[str]]:
    depth = name.count("/") + 1
    while True:
        kids = {f.path.split("/")[depth] for f in files if f.path.count("/") > depth}
        direct = [f for f in files if f.path.count("/") == depth]
        if len(kids) == 1 and not direct:
            depth += 1
            name = "/".join(files[0].path.split("/")[:depth])
            continue
        return name, depth, kids


def child_modules(
    files: list[SourceFile],
    name: str,
    depth: int,
) -> dict[str, list[SourceFile]]:
    out = {}
    for f in files:
        parts = f.path.split("/")
        key = "/".join(parts[: depth + 1]) if len(parts) > depth + 1 else name + FILES_SUFFIX
        out.setdefault(key, []).append(f)
    return out


def total_loc(
    files: list[SourceFile],
) -> int:
    return sum(f.loc for f in files)


def split_modules(
    src: list[SourceFile],
    policy: Policy,
    depth: Optional[int] = None,
    max_modules: Optional[int] = None,
) -> tuple[dict[str, list[SourceFile]], list[str]]:
    """Level 1 is the top-level folders. Each further level splits every module of `split_min_loc`+ lines into its
    sub-folders (a single-child chain counts as one step) or, in a flat folder of 2-`split_max_files` files, into one
    module per file. Largest modules split first, and no split may take the total past `max_modules`.
    Returns ({name: files}, notes)."""
    depth = policy.depth if depth is None else depth
    max_modules = policy.max_modules if max_modules is None else max_modules
    mods = {}
    for f in src:
        parts = f.path.split("/")
        mods.setdefault(parts[0] if len(parts) > 1 else ROOT_MODULE, []).append(f)
    notes = []
    for level in range(2, depth + 1):
        out, count = {}, len(mods)
        for name, files in sorted(mods.items(), key=lambda name_and_files: -total_loc(name_and_files[1])):
            if name == ROOT_MODULE or name.endswith(FILES_SUFFIX) or total_loc(files) < policy.split_min_loc:
                out[name] = files
                continue
            base, d, kids = descend_single_chains(name, files)
            if not kids and 2 <= len(files) <= policy.split_max_files:
                new, what = {f.path: [f] for f in files}, "one module per file, %d files" % len(files)
            elif len(kids) >= 2:
                new = child_modules(files, base, d)
                what = "%d modules" % len(new)
            else:
                out[name] = files
                continue
            if count - 1 + len(new) > max_modules:
                out[name] = files
                continue
            out.update(new)
            count += len(new) - 1
            notes.append("level %d: split %s (%s)" % (level, name, what))
        mods = out
    return mods, notes


def module_of(
    path: str,
    names: Container[str],
) -> Optional[str]:
    """The rated module a path belongs to, or None: the file itself when it is a module, else the deepest folder module
    that contains it (`<dir>/(files)` for files directly in a split folder), else `(root)` for a top-level file."""
    if path in names:
        return path
    parts = path.split("/")
    if len(parts) == 1:
        return ROOT_MODULE if ROOT_MODULE in names else None
    for i in range(len(parts) - 1, 0, -1):
        prefix = "/".join(parts[:i])
        if i == len(parts) - 1 and prefix + FILES_SUFFIX in names:
            return prefix + FILES_SUFFIX
        if prefix in names:
            return prefix
    return None


# What the knowledge graph says about modules: who depends on whom, their functions, and the tests linked to them.
# `file_mod` maps each source file to its module.


def dependents(
    graph: Graph,
    file_mod: dict[str, str],
) -> tuple[defaultdict[str, set[str]], defaultdict[str, set[str]]]:
    """(fan_in, fan_out): for each module, the modules that depend on it and the modules it depends on."""
    fan_in, fan_out = defaultdict(set), defaultdict(set)
    for sf, targets in graph.links.items():
        for tf in targets:
            ms, mt = file_mod.get(sf), file_mod.get(tf)
            if ms and mt and ms != mt:
                fan_out[ms].add(mt)
                fan_in[mt].add(ms)
    return fan_in, fan_out


def functions_by_module(
    graph: Graph,
    file_mod: dict[str, str],
) -> defaultdict[str, list[str]]:
    out = defaultdict(list)
    for qn, (kind, fp, is_test) in graph.nodes.items():
        if kind == "Function" and not is_test and fp in file_mod:
            out[file_mod[fp]].append(qn)
    return out


def test_files_to_modules(
    graph: Graph,
    file_mod: dict[str, str],
) -> defaultdict[str, set[str]]:
    out = defaultdict(set)
    for qn, test_files in graph.tested.items():
        kind, fp, _ = graph.nodes[qn]
        if kind == "Function" and fp in file_mod:
            for tf in test_files:
                out[tf].add(file_mod[fp])
    return out


# What one Python file says about risk: ast metrics, write-call patterns and TODO markers, read from text. Nothing is
# imported or run. Results are cached per file content, so rating many revisions of a repository parses each version of
# a file once.

TODO = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b")
# kind -> (call pattern, pattern the file must also match). The points per kind are business policy (Policy.effect_points).
WRITE_CALLS = {
    "db_write": (
        re.compile(
            r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+(TABLE|COLUMN|INDEX)|ALTER\s+TABLE|TRUNCATE)\b"
            r"|\bsession\.(add|delete|merge)\(|\.commit\(\)|bulk_insert",
            re.I,
        ),
        None,
    ),
    "aws_write": (
        re.compile(
            r"\.(put_object|delete_object|delete_objects|put_item|update_item|delete_item|send_message"
            r"|send_message_batch|publish|start_execution|invoke|put_parameter|delete_parameter|put_record"
            r"|terminate_\w+|create_\w+|delete_\w+|update_\w+)\("
        ),
        re.compile(r"boto3|botocore"),
    ),
    "es_write": (
        re.compile(r"\.(bulk|index|delete_by_query|update_by_query|delete)\("),
        re.compile(r"elasticsearch|opensearch", re.I),
    ),
    "http_write": (
        re.compile(r"\b(requests|httpx|client|session)\.(post|put|patch|delete)\("),
        re.compile(r"requests|httpx|aiohttp"),
    ),
    "fs_destructive": (re.compile(r"\b(os\.remove|os\.unlink|shutil\.rmtree|rmtree)\(|\.unlink\("), None),
    "fs_write": (re.compile(r"open\([^)]*['\"][wa]b?['\"]|\.write_text\(|\.write_bytes\("), None),
    "subprocess": (re.compile(r"\b(subprocess\.(run|Popen|call|check_output)|os\.system)\("), None),
}


def python_stats(
    text: str,
) -> Optional[PyStats]:
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError, RecursionError:
        return None
    st = PyStats()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", node.lineno)
            cx = 1
            for sub in ast.walk(node):
                if isinstance(sub, (ast.If, ast.For, ast.While, ast.AsyncFor, ast.ExceptHandler, ast.IfExp)):
                    cx += 1
                elif isinstance(sub, ast.BoolOp):
                    cx += len(sub.values) - 1
            args = [a for a in node.args.args + node.args.kwonlyargs if a.arg not in ("self", "cls")]
            st.funcs.append((end - node.lineno + 1, cx))
            st.typed += 1 if node.returns is not None and all(a.annotation is not None for a in args) else 0
        elif isinstance(node, ast.ExceptHandler):
            t = node.type
            if t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException")):
                st.broad += 1
            if all(isinstance(b, (ast.Pass, ast.Continue)) for b in node.body):
                st.swallow += 1
    return st


def write_calls(
    text: str,
) -> dict[str, tuple[int, int]]:
    """{kind: (count, first line)} of write-call patterns in one file."""
    out = {}
    for kind, (call, gate) in WRITE_CALLS.items():
        if gate and not gate.search(text):
            continue
        matches = list(call.finditer(text))
        if matches:
            out[kind] = (len(matches), text.count("\n", 0, matches[0].start()) + 1)
    return out


def file_signals(
    source_file: SourceFile,
    cache: Optional[dict[str, FileSignals]] = None,
) -> FileSignals:
    """The FileSignals of a SourceFile: function sizes, complexity, typing, exception handling, TODOs and write calls.
    `cache` ({content id: FileSignals}, e.g. a RatingCache's) is shared across revisions."""
    key = source_file.blob or source_file.text
    if cache is not None and key in cache:
        return cache[key]
    signals = FileSignals(
        stats=python_stats(source_file.text),
        todo=len(TODO.findall(source_file.text)),
        effects=write_calls(source_file.text),
    )
    if cache is not None:
        cache[key] = signals
    return signals


# The built-in knowledge graph, from source text alone (stdlib ast), for any revision and with nothing to install.
#
# Links are imports between repository files, resolved by path; functions and methods are nodes; a test file is linked
# to a function when it imports the function's file (following one package `__init__` re-export) and names the function.
# It cannot see execution flows (`Graph.has_flows` is False), so Business Impact leaves its flow terms out. Per-file
# syntax is cached by content, so many revisions of one repository parse each version of a file once.

STDLIB = frozenset(sys.stdlib_module_names)


def parse_syntax(
    text: str,
) -> FileSyntax:
    """The FileSyntax of a Python file (empty when it doesn't parse)."""
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError, RecursionError:
        return FileSyntax()
    defs, imports, refs = [], [], set()

    def visit(
        node: ast.AST,
        prefix: str,
    ) -> None:
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


# Where an import name can come from: (dotted name -> path, dotted suffix -> paths).
type ModuleIndex = tuple[dict[str, str], dict[str, list[str]]]


def dotted_name(
    path: str,
) -> str:
    p = path[:-3]
    if p == "__init__" or p.endswith("/__init__"):
        p = p[: -len("__init__")].rstrip("/")
    return p.replace("/", ".")


def folder_of(
    path: str,
) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def module_index(
    paths: Iterable[str],
) -> ModuleIndex:
    """Where an import name can come from in this repository: (dotted name -> path, dotted suffix -> paths)."""
    full, suffix = {}, defaultdict(list)
    for path in paths:
        dotted = dotted_name(path)
        if not dotted:
            continue
        full.setdefault(dotted, path)
        parts = dotted.split(".")
        for i in range(1, len(parts)):
            suffix[".".join(parts[i:])].append(path)
    return full, suffix


def is_import_root(
    candidate: str,
    dotted: str,
    importer: str,
) -> bool:
    """A suffix match counts when its import root (the folder the dotted name hangs from) is the importer's folder or an
    ancestor of it, or a top-level source folder such as `src` (installed-package layout)."""
    segments = candidate.split("/")
    covered = dotted.count(".") + 1 + (1 if segments[-1] == "__init__.py" else 0)  # `pkg` covers pkg/__init__.py
    root = "/".join(segments[:-covered])
    folder = folder_of(importer)
    return root.count("/") == 0 or folder == root or folder.startswith(root + "/")


def lookup(
    index: ModuleIndex,
    dotted: str,
    importer: str,
) -> Optional[str]:
    full, suffix = index
    if dotted in full:
        return full[dotted]
    cands = [c for c in suffix.get(dotted, ()) if is_import_root(c, dotted, importer)]
    if not cands:
        return None
    imp = importer.split("/")

    def shared(
        candidate: str,
    ) -> int:
        n = 0
        for a, b in zip(imp, candidate.split("/")):
            if a != b:
                break
            n += 1
        return n

    return max(cands, key=lambda candidate: (shared(candidate), -len(candidate)))


def resolve(
    index: ModuleIndex,
    importer: str,
    module: str,
    level: int,
    names: tuple[str, ...],
) -> set[str]:
    """The repository files an import of `importer` reaches."""
    full, _ = index
    out = set()
    if level:  # relative imports resolve exactly against the importer's package
        pkg = folder_of(importer).split("/") if folder_of(importer) else []
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


def builtin_graph(
    files: list[SourceFile],
    cache: Optional[dict[str, FileSyntax]] = None,
) -> Graph:
    """The Graph of the Python files: imports as links, functions as nodes, and the test files that use them.
    `cache` ({content id: FileSyntax}, e.g. a RatingCache's) is shared across revisions."""
    cache = {} if cache is None else cache

    def syntax_of(
        source_file: SourceFile,
    ) -> FileSyntax:
        key = source_file.blob or source_file.text
        if key not in cache:
            cache[key] = parse_syntax(source_file.text)
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
