# clean_eval/use_cases

This layer holds exactly two things; `UseCasesHoldInterfacesAndFunctions` in `tests/test_architecture.py` checks it.

**1. `interfaces.py`:** the abstract classes of every external utility the business logic uses. Every method is an
`@abstractmethod`, and its docstring is the contract implementations follow.
- `Git`, `CodeHost`, `FileSystem`, `KnowledgeGraph`, `Environment` and `Clock` are implemented by the gateways in
  `clean_eval/external/`.
- `DiagramPresenter` and `ReportPresenter` are implemented by the presenters in `clean_eval/adaptor/`.

**2. Everything else: module-level functions, no classes.**
- **Use cases** take implementations of the interfaces as parameters, e.g. `rate_working_tree(git, fs, clock,
  policy)`. They call only the interfaces' abstract methods, and never build a gateway or import an outer layer.
- **Shared helpers** sit beside them: `sources.py`, `store.py`, `coverage.py`, `tuning_files.py`.
- **`rules/`** holds the business rules: pure functions over domain models that take no interfaces.

**Constraints**
- Import only the domain, this layer, the stdlib modules in `PURE_IMPORTS`, and pydantic.
- No I/O and no clock reads; today's date comes from a `Clock`.
- Take only `sys.stdlib_module_names` from `sys`.

**When a use case needs something new from the outside world**
1. Declare it as an abstract method in `interfaces.py`, with its contract as the docstring.
2. Implement it in the gateway or presenter. Implementations expose nothing beyond their interface, and the
   architecture tests reject extra public methods.
3. Give the fakes the same behavior in `tests/use_cases/fakes.py` (`MemoryGit`, `MemoryFileSystem`).
4. Add a check for it to the contract tests in `tests/external/test_git.py` or `test_filesystem.py`, which run against
   both the fake and the gateway.

**Tests** go in `tests/use_cases/test_<module>.py` and `tests/use_cases/rules/test_<module>.py`. They use the fakes,
never real git, files or `gh`.
