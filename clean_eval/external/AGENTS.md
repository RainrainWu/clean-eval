# clean_eval/external

The only code that touches anything outside the process.

**Gateways:** one module, and one public `*Gateway` class, per external dependency.
- **Modules:** `git.py` (the git CLI), `github.py` (the gh CLI), `code_review_graph.py` (its CLI and the SQLite
  `graph.db` it builds), `filesystem.py`, `environment.py` and `clock.py`.
- **One interface each.** A gateway implements exactly one interface from `clean_eval/use_cases/interfaces.py` and
  exposes nothing beyond it.
- **Failures** map to the domain's errors: `HeatmapError`, or `RemoteError` with `kind` `not_found` or `error`.
- **What a gateway reads** is validated into domain models.

**Drivers:** only they use the console (`print`, `sys.stdin`/`stdout`/`exit`).
- `cli.py` is the command line.
- `ci.py` holds the action's `prepare`, `finalize`, `post` and `redact` steps.
- `agent_guard.py` is the CI agent's PreToolUse hook: an allowlist that fails closed.

**`wiring.py`:** the composition root. It builds the gateways and presenters the drivers hand to the use cases.

**What `ExternalDependenciesStayInTheirGateways` enforces** (in `tests/test_architecture.py`)
- Only a gateway imports modules that reach outside (`subprocess`, `sqlite3`, `os`, `shutil`, `pathlib`, ...), and only
  those its entry in `GATEWAYS` allows.
- `open()` appears only in `filesystem.py`, clock reads only in `clock.py`, and `os.environ` only in `environment.py`.

**To add an external dependency**
1. Define its interface in `use_cases/interfaces.py`.
2. Write `<dependency>.py` here, with one `*Gateway(<Interface>)` class.
3. Register its imports in `GATEWAYS` and the pair in `IMPLEMENTATIONS` (`tests/test_architecture.py`).
4. Build it in `wiring.py`.

**Safety rules** (`README.md` "Trust model")
- Git takes paths literally (`--literal-pathspecs`, NUL-delimited output).
- Code-host values are validated before use; a head commit must be an object id before it reaches git.
- Pair any change to `agent_guard.py` with a test in `tests/external/test_agent_guard.py`.

**Tests** go in `tests/external/test_<module>.py`. They run against the real dependency (throwaway repositories, temp
folders) or a fake executable (`tests.helpers.FakeBin` for `gh` and `claude`).
