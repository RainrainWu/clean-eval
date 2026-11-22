# clean_eval/use_cases

This layer holds exactly two things; `UseCasesHoldInterfacesAndFunctions` in `tests/test_architecture.py` checks it.
It is business logic only: it knows no external tool. What git, GitHub, GitHub Actions, Claude Code or a coverage
report look like (their commands, formats, field names, conventions, limits and error codes) is their gateway's, and
reaches this layer as domain models.

**1. `interfaces.py`:** the abstract classes of every external utility the business logic uses. Every method is an
`@abstractmethod`, and its docstring is the contract implementations follow.
- `Git`, `CodeHost`, `FileSystem`, `CoverageReport`, `Agent`, `Environment`, `Clock` and `Runner` are implemented by
  the gateways in `clean_eval/external/`.
- `DiagramPresenter` and `ReportPresenter` are implemented by the presenters in `clean_eval/adaptor/`.
- `RunLog`, a CI run's log, is implemented by the command line (`clean_eval/external/cli.py`): the console is the
  driver's.

**2. Everything else: module-level functions, no classes.**
- **Use cases** take implementations of the interfaces as parameters, e.g. `rate_working_tree(git, fs, coverage,
  clock, policy)`. They call only the interfaces' abstract methods, and never build a gateway or import an outer layer.
- **One module per feature:** `heatmap.py` (rate, level, summarize and publish the heat map), `review.py` (a change's
  risk, the CI reviewer's context, finalizing and posting the review) and `evaluation.py` (the merged-PR history, the
  eval's context and tuning files, finalizing the eval). Each also holds its skill's CI run (`run_heatmap_action`,
  `run_review_action`, `run_eval_action`), which the `action` command runs.
- **Shared:** `store.py` holds the files of a run, and `ci.py` the steps every CI run shares (its folders, the agent's
  run on its `AgentTask` and its redacted transcript, what the run reports to its `Runner`).
- **`rules/`** holds the business rules: pure functions over domain models that take no interfaces. They follow the
  same features (`heatmap.py`, `review.py`, `evaluation.py`), plus `analysis.py` for reading code.

**Constraints**
- Import only the domain, this layer, the stdlib modules in `PURE_IMPORTS`, pydantic, and `clean_eval.settings`. A use
  case takes the run's `Settings` as a parameter and never builds one, since building it reads the environment.
- No I/O and no clock reads; today's date comes from a `Clock`.
- Take only `sys.stdlib_module_names` from `sys`.

**When a use case needs something new from the outside world**
1. Declare it as an abstract method in `interfaces.py`, with its contract as the docstring, in the domain's terms: a
   neutral model in, a neutral model out (a `ReviewPost` with `request_changes`, never GitHub's `event`).
2. Implement it in the gateway or presenter. Implementations expose nothing beyond their interface, and the
   architecture tests reject extra public methods.
3. Let the tests stand in for it. A new interface gets a mock fixture in `tests/use_cases/conftest.py`. A new method
   of `Git` or `FileSystem` gets the same behavior in the in-memory fakes behind the `git` and `fs` mocks
   (`MemoryGit`, `MemoryFileSystem` in `tests/use_cases/fakes.py`).
4. For `Git` and `FileSystem`, add a check to the contract tests in `tests/external/test_git.py` or
   `test_filesystem.py`, which run against both the fake and the gateway.

**Tests** go in `tests/use_cases/test_<module>.py` and `tests/use_cases/rules/test_<module>.py`. A use case's tests
pass it the MagicMock fixtures of `tests/use_cases/conftest.py`, never real git, files or `gh`.
