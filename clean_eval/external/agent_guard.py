"""PreToolUse hook for the CI agent (`clean_eval guard`): an allowlist that fails closed.

Second layer behind `claude --restricted` (which already removes code-running tools and confines file tools). This hook
does not depend on how permission rules are interpreted: it resolves real paths (so a symlink in the checkout can't point
the agent at /proc/self/environ or ~/.ssh) and allows only:

  Read / Grep / Glob / LS        real path under a read root (or a write root), never inside .git
  Write / Edit / MultiEdit       real path under a write root
  TodoWrite / ToolSearch         no file access
  everything else (Bash, WebFetch, Task, NotebookEdit, Skill, MCP tools, ...)   denied

Skill is denied because a skill is instructions, and the checkout's skills are as untrusted as the rest of it (the
agent reads clean-eval's own skills from the precomputed context, with Read).

Roots come from the environment the Claude Code gateway gives the agent (read through the environment gateway; paths are resolved
through the file-system gateway):
  CE_GUARD_READ_ROOTS   os.pathsep-separated absolute directories
  CE_GUARD_WRITE_ROOTS  os.pathsep-separated absolute directories

Protocol: the tool call as JSON on stdin; exit 0 = allow, exit 2 = deny (stderr is shown to the agent). Anything
unexpected denies; the hook command the gateway sets also turns any other exit status into a denial (`|| exit 2`).
"""

import sys
from typing import (
    Any,
    Optional,
    TextIO,
)

from clean_eval.domain.review import ToolCall
from clean_eval.external.environment import EnvironmentGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.use_cases.interfaces import (
    Environment,
    FileSystem,
)

READ_TOOLS = frozendict({"Read": "file_path", "Grep": "path", "Glob": "path", "LS": "path"})
WRITE_TOOLS = frozendict({"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"})
SAFE_TOOLS = frozenset({"TodoWrite", "ToolSearch"})
ALLOW, DENY = 0, 2


def roots(
    paths: list[str],
    fs: FileSystem,
) -> list[str]:
    """The real paths of the given roots. A symlinked root would make its target reachable, so it is dropped (fail
    closed)."""
    return [fs.real_path(p) for p in paths if not fs.is_link(p.rstrip(fs.sep))]


def is_within(
    path: str,
    allowed: list[str],
    sep: str,
) -> bool:
    return any(path == r or path.startswith(r.rstrip(sep) + sep) for r in allowed)


def decide(
    tool: str,
    tool_input: dict[str, Any],
    cwd: str,
    read_roots: list[str],
    write_roots: list[str],
    fs: FileSystem,
) -> Optional[str]:
    """Return None to allow, or a reason string to deny."""
    if tool in SAFE_TOOLS:
        return None
    if tool in READ_TOOLS or tool in WRITE_TOOLS:
        key = READ_TOOLS.get(tool) or WRITE_TOOLS[tool]
        raw = tool_input.get(key) or (cwd if tool in READ_TOOLS else None)
        if not raw or not isinstance(raw, str):
            return "%s: missing %s" % (tool, key)
        path = fs.real_path(fs.join(cwd, fs.expand_user(raw)))
        if ".git" in path.split(fs.sep):
            return "%s: %s is inside a .git directory" % (tool, raw)
        for k in ("pattern", "glob") if tool in ("Glob", "Grep") else ():
            v = tool_input.get(k)
            # a glob is resolved relative to `path`: absolute, parent-relative or brace-expanded globs could escape it,
            # and a .git segment would target the token actions/checkout may persist there.
            # (Recursive Grep/Glob from the checkout root skip .git — verified with Claude Code 2.1.328.)
            if (tool == "Glob" or k == "glob") and isinstance(v, str):
                if v.startswith(("/", "~")) or ".." in v or "{" in v:
                    return "%s: %s must be a plain relative glob (no '/', '~', '..' or braces)" % (tool, k)
                if ".git" in v.replace("\\", "/").split("/"):
                    return "%s: %s may not target .git" % (tool, k)
        allowed = write_roots if tool in WRITE_TOOLS else read_roots + write_roots
        if not is_within(path, allowed, fs.sep):
            kind = "write" if tool in WRITE_TOOLS else "read"
            return "%s: %s (resolves to %s) is outside the allowed %s directories" % (tool, raw, path, kind)
        return None
    return "%s is not available to the clean-eval CI agent" % tool


def hook(
    stdin: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
    env: Optional[Environment] = None,
    fs: Optional[FileSystem] = None,
) -> int:
    """Decide one tool call (default: the hook protocol on this process's stdin and stderr, with its environment and
    file system); the exit status."""
    stdin, stderr = stdin or sys.stdin, stderr or sys.stderr
    env, fs = env or EnvironmentGateway(), fs or FileSystemGateway()
    try:
        call = ToolCall.model_validate_json(stdin.read())
        reason = decide(
            call.tool_name,
            call.tool_input,
            call.cwd or fs.cwd(),
            roots(env.paths("CE_GUARD_READ_ROOTS"), fs),
            roots(env.paths("CE_GUARD_WRITE_ROOTS"), fs),
            fs,
        )
    except Exception as e:  # fail closed on anything unexpected, a ToolCall that doesn't validate included
        reason = "unreadable hook input (%s)" % str(e).splitlines()[0]
    if reason:
        print("clean-eval guard: %s" % reason, file=stderr)
        return DENY
    return ALLOW
