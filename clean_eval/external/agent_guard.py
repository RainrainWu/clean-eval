"""PreToolUse hook for the CI agent (`clean_eval guard`): an allowlist that fails closed.

Second layer behind `claude --restricted` (which already removes code-running tools and confines file tools). This hook
does not depend on how permission rules are interpreted: it resolves real paths (so a symlink in the checkout can't point
the agent at /proc/self/environ or ~/.ssh) and allows only:

  Read / Grep / Glob / LS        real path under a read root (or a write root), never inside .git
  Write / Edit / MultiEdit       real path under a write root
  Skill / TodoWrite / ToolSearch no file access
  everything else (Bash, WebFetch, Task, NotebookEdit, MCP tools, ...)   denied

Roots come from the environment action.sh gives the agent:
  CE_GUARD_READ_ROOTS   os.pathsep-separated absolute directories
  CE_GUARD_WRITE_ROOTS  os.pathsep-separated absolute directories

Protocol: the tool call as JSON on stdin; exit 0 = allow, exit 2 = deny (stderr is shown to the agent). Anything
unexpected denies; action.sh also turns any other exit status of the hook command into a denial.
"""
import json
import os
import sys

READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path", "LS": "path"}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"}
SAFE_TOOLS = {"Skill", "TodoWrite", "ToolSearch"}
ALLOW, DENY = 0, 2


def roots(environ, var):
    # a symlinked root would make its target reachable, so such roots are dropped (fail closed)
    return [os.path.realpath(p) for p in environ.get(var, "").split(os.pathsep)
            if p.strip() and not os.path.islink(p.rstrip(os.sep))]


def _within(path, allowed):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in allowed)


def decide(tool, tool_input, cwd, read_roots, write_roots):
    """Return None to allow, or a reason string to deny."""
    if tool in SAFE_TOOLS:
        return None
    if tool in READ_TOOLS or tool in WRITE_TOOLS:
        key = READ_TOOLS.get(tool) or WRITE_TOOLS[tool]
        raw = tool_input.get(key) or (cwd if tool in READ_TOOLS else None)
        if not raw or not isinstance(raw, str):
            return "%s: missing %s" % (tool, key)
        path = os.path.realpath(os.path.join(cwd, os.path.expanduser(raw)))
        if ".git" in path.split(os.sep):
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
        if not _within(path, allowed):
            kind = "write" if tool in WRITE_TOOLS else "read"
            return "%s: %s (resolves to %s) is outside the allowed %s directories" % (tool, raw, path, kind)
        return None
    return "%s is not available to the clean-eval CI agent" % tool


def hook(stdin=None, stderr=None, environ=None):
    """Decide one tool call (default: the hook protocol on this process's stdin and stderr); the exit status."""
    stdin, stderr, environ = stdin or sys.stdin, stderr or sys.stderr, os.environ if environ is None else environ
    try:
        event = json.load(stdin)
        tool = event.get("tool_name") or ""
        tool_input = event.get("tool_input") or {}
        cwd = event.get("cwd") or os.getcwd()
        if not isinstance(tool_input, dict) or not isinstance(tool, str) or not isinstance(cwd, str):
            raise ValueError("unexpected hook input")
        reason = decide(tool, tool_input, cwd, roots(environ, "CE_GUARD_READ_ROOTS"), roots(environ, "CE_GUARD_WRITE_ROOTS"))
    except Exception as e:  # fail closed on anything unexpected
        reason = "unreadable hook input (%s)" % e
    if reason:
        print("clean-eval guard: %s" % reason, file=stderr)
        return DENY
    return ALLOW
