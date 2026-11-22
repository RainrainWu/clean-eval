# tests

The tests are written for pytest, laid out like the package, and held to the same dependency rule.
`test_architecture.py` checks them:
- `TestsMirrorThePackage` checks the layout and the imports;
- `TestsFollowTheConventions` checks the shape described under "Writing tests";
- `TestCodeStyle` checks the code style of the root AGENTS.md here as everywhere: no `def _name`, no function over
  50 lines, none defined inside another, one parameter per line, one argument per line in a class constructor call,
  one name per line in an import of several, no `Literal` of names where a `StrEnum` belongs, no mutable constant, no
  single-character parameter names.

```bash
uv run pytest                                            # everything, ~30 s; no network (fake gh and claude)
uv run pytest -m "not e2e"                               # skip the subprocess end-to-end tests, ~5 s
uv run pytest tests/use_cases/test_heatmap.py            # one module; ::TestClass::test_x for one test, -k to filter
```

## Layout

- **Naming.** `tests/<layer>/.../test_<module>.py` tests `clean_eval/<layer>/.../<module>.py`. Name a new test module
  after the module it tests.
- **Imports.** A layer's tests import only that layer and the inner ones, from `clean_eval` and from `tests` alike. So
  use-case tests run against mocks of the interfaces, never real git, files or `gh`.
- **Fakes and gateways agree.** In `tests/external/test_git.py` and `test_filesystem.py`, the contract checks take a
  fixture parametrized over the in-memory fake and the gateway, so each check runs on both. When either changes,
  extend the contract.
- **Top-level tests** span the layers: `test_architecture.py` (the architecture rules), `test_action.py`
  (the GitHub Action's command end to end, run by its `action.yml` line with fake `claude` and `gh`),
  `test_settings.py` (`clean_eval/settings.py`, which sits beside the layers, and its match with `action.yml`) and
  `test_release.py` (the release check).
- **Packages.** Every test folder needs an `__init__.py`: the layers' test modules share names (`test_review.py`,
  `test_heatmap.py`), and the tests import `tests.*`.
- **Support** holds classes and constants only, no functions. Read and write files with pathlib.
  - `tests/helpers.py` (no package imports): `Repo`, `FakeBin`, `Dates`, `Contents` (behind `write_files`) and
    `CommandLine` (behind `run_cli`).
  - `tests/external/fakes.py`: `ClaudeProcess` and `TokenEndpoint`, the claude process and the OIDC endpoint behind
    `claude_runs` and `oidc_tokens`.
  - `tests/use_cases/fakes.py`: `MemoryFileSystem`, `MemoryGit`, and `SquashSubjects`, how the `host` mock numbers
    the PRs of the tests' histories.
  - `tests/architecture.py`: `Codebase`, the code as `test_architecture.py` reads it.
  - `tests/action.py`: `Action`, which runs the action's command for `test_action.py`.

## Where the fixtures are

| conftest.py | Fixtures |
|---|---|
| `tests/` | The machine: `repo`, `fake_bin`, the autouse `hermetic_git`. A run's environment: `environ` (its variables), `settings_environment` (this process's environment made exactly that, for the test) and `settings` (the `Settings` read from it). Domain models: `today`, `policy`, `facts`, `source_files`, `ratings`, `rating`, `heatmap`, `judgement`. A change under review: `review_rules` (the rules it reads, as one text) and clean-eval's `rule_packs`, `finding`, `agent_review`, `risk_report`, `pull_request` (as gh prints it). A CI run's `agent_task`. The top-level tests: `codebase`; the action's `workspace`, `action`, `action_run` and the fakes' replies; the release check's `released`, `release_check`. Static assets: the settings tests' runs (`anthropic_api_key`, `review_run`, `retro_run`, `action_yml`); the fake `github_token`; the architecture's rule tables (`architecture`) and planted sources (`planted_*`); the action's workspaces (`*_workspace`), the fakes' replies and plans (`*_replies`, `*_plan`, `discounts_pull_request`) and `retro_since`; the release check's steps (`annotated_pre_release`, `forgotten_bump`, ...). |
| `tests/domain/` | Raw field dicts of a valid example per model (`scores`, `module_record`, `stored_review`, `review_rule`, `host_pull_request`, `period`, `merged_pr`, `retro_result`), which a test validates itself, so a refused input fails inside `pytest.raises`. Static assets: `default_policy`, `untidy_judgement`, `trusted_blocker`, `trusted_major`, `accepted_model_change`. |
| `tests/adaptor/` | Static assets: `mixed_ratings`; the review comment the template shows (`review_comment`) and the template itself (`comment_template`). |
| `tests/use_cases/` | The interfaces as MagicMocks (`fs`, `git`, `clock`, `env`, `host`, `coverage`, `agent`, `runner`, `run_log`, `diagram_presenter`, `report_presenter`) and the data they answer from (`files`, `links`, `history`, `shas`; `env` answers from `environ`, every value a secret; `coverage` from its param); `graph`, `rating_cache`, `period`; the class-scoped `collected` history. Static assets: a CI run's `run_folders`, `files_before_a_run` and `run_environment`; each module's sources, histories, changes and stored files (`core_source`, `core_history`, `core_line_rates`, `review_threads`, `policy_file`, ...). |
| `tests/use_cases/rules/` | The rules' inputs as models: `aspect_scores`, `merged_prs`. Static assets: the rules' inputs as text and models (`package_repository`, `readme_block`, the `Secrets` a text is cleaned of (`token_secrets`, `nested_secrets`, `framed_secrets`), `legacy_review_rules`, ...). |
| `tests/external/` | The real gateways and repositories of the contract tests (`git`, `fs`, `repo`, `history`, `files`); the mocked CLIs (`gh`; and for the Claude Code gateway `claude_runs`, `npm_installs` and `oidc_tokens`, GitHub's token endpoint); the guard's `sandbox`, `stdin`, `guard_env`; the command line's `run_cli` (a subprocess), `cli_runner` (click, in-process), `host` and the CI commands' files and arguments. Static assets: the Claude Code gateway's environments (`agent_variables`, `federated_job`, ...) and `skills_folder`, a runner's tokens (`github_app_token`, `github_app_token_basic`), the git gateway's histories (`*_history`) and diffs (`diff_of_x_and_y`, ...), gh's payloads (`review_threads_view`, `review_pages`, ...) and what posting a review did (`posted_after_a_refusal`, `refused_every_time`), coverage reports (`coverage_report`, ...), and the CI commands' edits and findings (`valid_model_edit`, `blocker_finding`, ...). |

## Writing tests

### A test module

- **What it holds:** a docstring, imports and `Test*` classes, plus `pytestmark` when it needs one. Nothing else at
  module level: no constant, no helper, no fixture, no test outside a class.
- **Static assets are fixtures.** What a test feeds the code or checks against (an environment, files, a repository's
  history, a payload, a model, an expected structure) is a fixture in the conftest.py at the level of its tests, with
  a docstring saying what it is. A decorator names it with pytest-lazy-fixtures' `lf`, directly or as an indirect
  param, and derives a variant with `lfc`:
  - `@pytest.mark.parametrize("environ", [lf("federated_job")], indirect=True)`;
  - `lfc(lambda job: {**job, "ANTHROPIC_ORGANIZATION_ID": None}, lf("federated_job"))`.
  A case's own scalars (an expected message, a variable's name, a count), its `ids`, and a small literal only that
  case uses stay inline, in its decorator or its test.
- **One class per unit or behaviour** (`TestPolicyAt`, `TestCollectHistory`), with a docstring saying what it covers.
  A class holds only `test_*` methods: no helper methods, no fixtures, no setup.
- **Plain `assert`** and `pytest.raises`. From unittest, only `unittest.mock`.
- **`@pytest.mark.parametrize`** for a table of cases, so each row runs and reports on its own. Give rows readable
  `ids`.
- **Mark subprocess end-to-end tests `e2e`** (`pytestmark = pytest.mark.e2e`), so `-m "not e2e"` stays fast.

### Fixtures

- **Fixtures live in `conftest.py`**, at the level of the tests that share them:
  - `tests/conftest.py` for every layer (domain models, repositories, fake executables) and for the top-level tests;
  - `tests/<layer>/conftest.py` for one layer, and a deeper `conftest.py` for one folder.
  Put a fixture as low as its users allow, and move it up when another folder needs it.
- **Customise a fixture through `request.param`.**
  - The fixture reads `getattr(request, "param", <default>)`.
  - A test sets it with `@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test method or on
    the class (every test in the class gets it).
  - pytest refuses a method that parametrizes a name its class already does, so give such a test a class of its own.
  - `indirect=["history"]` mixes an indirect param with plain ones.
- **Data fixtures feed dependency fixtures.**
  - Data fixtures hold the inputs: `files`, `history`, `today`, `environ`, `policy`, `facts`, `source_files`,
    `ratings`, `judgement`.
  - The fixtures built on them follow their customisation: `fs` from `files`, `git` and `shas` from `history`,
    `clock` from `today`, `heatmap` and `rating` from `ratings`.
- **Design a fixture for reuse:** a sensible default, one obvious shape for its param, and a docstring that says both.
- **Several variants in one test:** derive them from the fixture's value with the model's own API
  (`facts.model_copy(update={"tooling": True})`).
- **Expensive shared state** gets a wider `scope` (`"module"`, `"class"`). Parametrize it with `scope="class"` when its
  input varies by class.

### Mocks

- **External dependencies are MagicMocks, injected.** A use case takes its dependencies as arguments, and its tests
  pass the fixtures of `tests/use_cases/conftest.py`:
  - `fs` and `git` are `MagicMock(spec=<interface>, wraps=<in-memory fake>)`. They answer like a file system and a
    repository, with contents from `files` / `links` and `history`. `memory_fs` and `memory_git` are the fakes
    behind them, to check what was left behind without recording a call.
  - `clock` (from `today`), `env` (from `environ`), `host`, `agent`, `run_log`, `diagram_presenter` and
    `report_presenter` are `create_autospec(<interface>, instance=True)`, so a call that doesn't match the interface
    fails. `request.param` configures them, as `configure_mock`'s keywords:
    `{"review_threads.return_value": (view, inline), "post_review.side_effect": RemoteError("...")}`.
- **Assert on the calls** (`host.dismiss_review.assert_called_once_with(...)`, `git.diff.call_args_list`), and make
  one call fail with `side_effect`.
- **Gateways** (`tests/external/`):
  - Mock what a gateway calls out to (`subprocess.run` of `gh` or `claude`, the environment) when the test
    is about the gateway's own logic.
  - The git and file-system gateways run against real throwaway repositories and folders. Their contract tests check
    that the in-memory fakes behave like the real thing, which a mock can't.
- **End to end** (`test_action.py`): the action's command runs in a subprocess with fake `gh` and `claude`
  executables (`fake_bin`), so the real Claude Code gateway's sandbox (flags, environment, settings, hook) is checked
  as a whole.

### Strictness and types

- **Strictness.** Warnings fail the suite, and markers must be registered in `pyproject.toml`.
- **Types.** ty checks the tests too, though their own functions need no annotations.
  - Narrow a value before you use it: `assert x is not None` for an `Optional`, `isinstance` for a union.
  - Build a deliberately invalid model with `Model.model_validate({...})`, so the call itself type-checks.
