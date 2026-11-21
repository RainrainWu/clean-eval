#!/usr/bin/env python3
"""PreToolUse hook for the CI agent: an allowlist that fails closed.

Second layer behind `claude --restricted` (which already removes code-running tools and confines
file tools). This hook does not depend on how permission rules are interpreted: it resolves real
paths (so a symlink in the checkout can't point the agent at /proc/self/environ or ~/.ssh) and
allows only:

  Read / Grep / Glob / LS        real path under a read root (or the write root), never inside .git
  Write / Edit / MultiEdit       real path under a write root
  Skill / TodoWrite / ToolSearch no file access
  everything else (Bash, WebFetch, Task, NotebookEdit, MCP tools, ...)   denied

Roots come from the environment run.sh gives the agent:
  CE_GUARD_READ_ROOTS   os.pathsep-separated absolute directories
  CE_GUARD_WRITE_ROOTS  os.pathsep-separated absolute directories

Protocol: JSON on stdin; exit 0 = allow, exit 2 = deny (stderr is shown to the agent).
"""
import json
import os
import sys

READ_TOOLS = {"Read": "file_path", "Grep": "path", "Glob": "path", "LS": "path"}
WRITE_TOOLS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path"}
SAFE_TOOLS = {"Skill", "TodoWrite", "ToolSearch"}


def _roots(var):
    # a symlinked root would make its target reachable, so such roots are dropped (fail closed)
    return [os.path.realpath(p) for p in os.environ.get(var, "").split(os.pathsep)
            if p.strip() and not os.path.islink(p.rstrip(os.sep))]


def _within(path, roots):
    return any(path == r or path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def decide(tool, tool_input, cwd, read_roots, write_roots):
    """Return None to allow, or a reason string to deny."""
    if tool in SAFE_TOOLS:
        return None
    if tool in READ_TOOLS or tool in WRITE_TOOLS:
        key = READ_TOOLS.get(tool) or WRITE_TOOLS[tool]
        raw = tool_input.get(key) or (cwd if tool in READ_TOOLS else None)
        if not raw or not isinstance(raw, str):
            return f"{tool}: missing {key}"
        path = os.path.realpath(os.path.join(cwd, os.path.expanduser(raw)))
        if ".git" in path.split(os.sep):
            return f"{tool}: {raw} is inside a .git directory"
        for k in ("pattern", "glob") if tool in ("Glob", "Grep") else ():
            v = tool_input.get(k)
            # a glob is resolved relative to `path`: absolute, parent-relative or brace-expanded globs could
            # escape it, and a .git segment would target the token actions/checkout may persist there.
            # (Recursive Grep/Glob from the checkout root skip .git — verified with Claude Code 2.1.328.)
            if (tool == "Glob" or k == "glob") and isinstance(v, str):
                if v.startswith(("/", "~")) or ".." in v or "{" in v:
                    return f"{tool}: {k} must be a plain relative glob (no '/', '~', '..' or braces)"
                if ".git" in v.replace("\\", "/").split("/"):
                    return f"{tool}: {k} may not target .git"
        allowed = write_roots if tool in WRITE_TOOLS else read_roots + write_roots
        if not _within(path, allowed):
            kind = "write" if tool in WRITE_TOOLS else "read"
            return f"{tool}: {raw} (resolves to {path}) is outside the allowed {kind} directories"
        return None
    return f"{tool} is not available to the clean-eval CI agent"


def main():
    try:
        event = json.load(sys.stdin)
        tool = event.get("tool_name") or ""
        tool_input = event.get("tool_input") or {}
        cwd = event.get("cwd") or os.getcwd()
        if not isinstance(tool_input, dict):
            raise ValueError("tool_input is not an object")
    except Exception as e:  # fail closed on anything unexpected
        print(f"clean-eval guard: unreadable hook input ({e})", file=sys.stderr)
        sys.exit(2)
    reason = decide(tool, tool_input, cwd, _roots("CE_GUARD_READ_ROOTS"), _roots("CE_GUARD_WRITE_ROOTS"))
    if reason:
        print(f"clean-eval guard: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
