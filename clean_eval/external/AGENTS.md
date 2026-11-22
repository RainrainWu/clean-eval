# clean_eval/external

The only code that touches anything outside the process.

**Gateways:** one public `*Gateway` class per external dependency.
- **Modules:** `git.py` (the git CLI), `github.py` (the gh CLI), `claude_code.py` (the CI agent: the claude CLI, run
  sandboxed, its npm install, and the job's OIDC token for keyless sign-in), `filesystem.py`, and `environment.py`
  (environment variables, and the clock).
- **One interface each.** A gateway implements exactly one interface from `clean_eval/use_cases/interfaces.py` and
  exposes nothing beyond it.
- **Failures** map to the domain's errors: `HeatmapError`, or `RemoteError` with `kind` `not_found` or `error`.
- **What a gateway reads** is validated into domain models.

**Drivers:** only they use the console (`print`, `sys.stdin`/`stdout`/`exit`).
- `cli.py` is the command line, built on click, and the entry point of every utility: the user commands, and the
  CI commands (`action`, which action.yml runs, and `prepare`, `finalize`, `post`, `redact`, `guard`). It is also the
  composition root: it builds the gateways and presenters it hands to the use cases, and `action` builds the run's
  `Settings` (`clean_eval/settings.py`) from the environment. A CI run's log (`RunLog`) is its console.
  - A new utility is a new click command of the `clean_eval` group, with its options declared on it.
  - A command that fails with a status calls `context.exit(<status>)`. A `HeatmapError` it raises reads
    `clean_eval: <message>` with status 1, never a traceback.
- `agent_guard.py` is the CI agent's PreToolUse hook: an allowlist that fails closed.

**What `ExternalDependenciesStayInTheirGateways` enforces** (in `tests/test_architecture.py`)
- Only a gateway module imports modules that reach outside (`subprocess`, `os`, `shutil`, `pathlib`, ...),
  and only those its entry in `GATEWAYS` allows.
- A gateway module holds exactly the gateway classes its `GATEWAYS` entry lists.
- `open()` appears only in `filesystem.py`, and clock reads and `os.environ` only in `environment.py`.
- `Settings` reads the environment too, through pydantic-settings, which only `clean_eval/settings.py` imports; only
  the drivers build a `Settings` (`TestTheSettings`).

**To add an external dependency**
1. Define its interface in `use_cases/interfaces.py`.
2. Write `<dependency>.py` here, with one `*Gateway(<Interface>)` class.
3. Register the module's imports and gateway classes in `GATEWAYS`, and the pair in `IMPLEMENTATIONS`
   (`tests/test_architecture.py`).
4. Build it in `cli.py`, the composition root.

**Safety rules** (`README.md` "Trust model")
- Git takes paths literally (`--literal-pathspecs`, NUL-delimited output).
- Code-host values are validated before use; a head commit must be an object id before it reaches git.
- Pair any change to `agent_guard.py` with a test in `tests/external/test_agent_guard.py`.

**Tests** go in `tests/external/test_<module>.py`; their fixtures are in `tests/external/conftest.py`.
- The git and file-system gateways run against real throwaway repositories and folders, in contract tests that the
  in-memory fakes pass too.
- The `gh` gateway gets a MagicMock of `subprocess.run` (the `gh` fixture). The Claude Code gateway gets them of
  `subprocess.Popen`, `subprocess.run` and `urlopen` (`claude_runs`, `npm_installs`, `oidc_tokens`).
- The guard resolves real paths; the command line gets its code host as a mock (the `host` fixture).
