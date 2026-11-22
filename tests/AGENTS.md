# tests

The tests are laid out like the package and held to the same dependency rule. `TestsMirrorThePackage` in
`test_architecture.py` checks the layout.

- **Naming.** `tests/<layer>/.../test_<module>.py` tests `clean_eval/<layer>/.../<module>.py`. Name a new test module
  after the module it tests.
- **Imports.** A layer's tests import only that layer and the inner ones, from `clean_eval` and from `tests` alike. So
  use-case tests run against fakes of the interfaces, never real git, files or `gh`. The fakes are in
  `tests/use_cases/fakes.py`:
  - `fake(Interface, method=...)` builds a one-off fake; calling a method it doesn't define fails the test.
  - `MemoryGit` is a linear history, built with `commit(subject, files, when=..., remove=...)`.
  - `MemoryFileSystem` holds files in memory; `link()` makes symlinks.
- **Fakes and gateways agree.** `tests/external/test_git.py` and `test_filesystem.py` run the same contract checks
  against each fake and its gateway. When either changes, extend the contract.
- **Support modules:**
  - `helpers.py` has no package imports: `Repo` (throwaway git repositories with dated commits), `FakeBin` (fake `gh`
    and `claude` executables) and file helpers.
  - `domain/builders.py` builds domain models.
  - `use_cases/fakes.py` holds the fakes.
- **Packages.** Every test folder needs an `__init__.py`, because unittest only discovers packages.
- **Top-level tests** span the layers: `test_architecture.py` (the architecture rules), `test_action_sh.py`
  (`action.sh` end to end, with fake `claude` and `gh`) and `test_release.py` (the release check).
- **Hermetic git.** `tests/__init__.py` makes every git call ignore the developer's git config.
- **Running.** Run from the repository root, naming tests as `tests.<path>`:

```bash
uv run python -m unittest discover -s tests -t .
uv run python -m unittest tests.use_cases.test_rate_repository.RateRepositoryTest
```
