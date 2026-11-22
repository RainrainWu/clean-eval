"""What one Python file says about risk: ast metrics, write-call patterns and TODO markers, read from text. Nothing is
imported or run. Results are cached per file content, so rating many revisions of a repository parses each version of
a file once."""

import ast
import re

from clean_eval.domain.source import FileSignals, PyStats

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


def python_stats(text):
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


def write_calls(text):
    """{kind: (count, first line)} of write-call patterns in one file."""
    out = {}
    for kind, (call, gate) in WRITE_CALLS.items():
        if gate and not gate.search(text):
            continue
        matches = list(call.finditer(text))
        if matches:
            out[kind] = (len(matches), text.count("\n", 0, matches[0].start()) + 1)
    return out


def file_signals(f, cache=None):
    """The FileSignals of a SourceFile: function sizes, complexity, typing, exception handling, TODOs and write calls.
    `cache` ({content id: FileSignals}, e.g. a RatingCache's) is shared across revisions."""
    key = f.blob or f.text
    if cache is not None and key in cache:
        return cache[key]
    signals = FileSignals(stats=python_stats(f.text), todo=len(TODO.findall(f.text)), effects=write_calls(f.text))
    if cache is not None:
        cache[key] = signals
    return signals
