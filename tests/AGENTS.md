# tests

The tests are written for pytest, laid out like the package, and held to the same dependency rule.
`TestsMirrorThePackage` in `test_architecture.py` checks the layout and the imports.

```bash
uv run pytest                                            # everything, ~30 s; no network (fake gh and claude)
uv run pytest -m "not e2e"                               # skip the subprocess end-to-end tests, ~5 s
uv run pytest tests/use_cases/test_sources.py            # one module; ::test_helpers for one test, -k <words> to filter
```

## Layout

- **Naming.** `tests/<layer>/.../test_<module>.py` tests `clean_eval/<layer>/.../<module>.py`. Name a new test module
  after the module it tests.
- **Imports.** A layer's tests import only that layer and the inner ones, from `clean_eval` and from `tests` alike. So
  use-case tests run against fakes of the interfaces, never real git, files or `gh`.
- **Fakes and gateways agree.** In `tests/external/test_git.py` and `test_filesystem.py`, the contract checks take a
  fixture parametrized over the fake and the gateway, so each check runs on both. When either changes, extend the
  contract.
- **Top-level tests** span the layers: `test_architecture.py` (the architecture rules), `test_action_sh.py`
  (`action.sh` end to end, with fake `claude` and `gh`) and `test_release.py` (the release check).
- **Packages.** Every test folder needs an `__init__.py`: the layers' test modules share names (`test_history.py`,
  `test_rating.py`), and the tests import `tests.*`.

## Writing tests

- **Plain `assert`** and `pytest.raises`, never unittest; ruff's `PT` rules and `test_architecture.py` enforce it.
- **Fixtures, not setup code.** Ask for what a test needs by parameter name:
  - pytest's own: `tmp_path` for files, `monkeypatch` for environment variables;
  - `tests/conftest.py`: `repo` (a throwaway git repository, `tests.helpers.Repo`), `fake_bin` (fake `gh` and
    `claude` first on `PATH`). The autouse `hermetic_git` makes every git call ignore the developer's git config.
  - `tests/use_cases/conftest.py`: `fs` (an empty `MemoryFileSystem`) and `git` (an empty `MemoryGit`). A module that
    needs contents overrides one with a fixture of the same name that asks for it and fills it.
- **The fakes** (`tests/use_cases/fakes.py`):
  - `fake(Interface, method=...)` builds a one-off fake; calling a method it doesn't define fails the test.
  - `MemoryGit` is a linear history, built with `commit(subject, files, when=..., remove=...)`.
  - `MemoryFileSystem` holds files in memory; `link()` makes symlinks.
- **`@pytest.mark.parametrize`** for a table of cases, so each row runs and reports on its own. Give rows readable
  `ids`.
- **Mark subprocess end-to-end tests `e2e`** (`pytestmark = pytest.mark.e2e`), so `-m "not e2e"` stays fast.
- **Expensive shared state** goes in a wider-scoped fixture: `scope="module"` for a module-level fixture (as
  `collected` in `tests/use_cases/test_collect_history.py`), or a `scope="class"` classmethod fixture.
- **Strictness.** Warnings fail the suite, and markers must be registered in `pyproject.toml`.
- **Builders.** `tests/domain/builders.py` builds domain models, and `tests/helpers.py` (no package imports) holds
  `Repo`, `FakeBin` and file helpers.
