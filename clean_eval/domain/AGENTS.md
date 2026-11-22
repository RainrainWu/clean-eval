# clean_eval/domain

Data models only: pydantic models of the policy, the code read, the ratings and heat map, a change's risk, reviews, the
merged-PR history and a retro's outcome, plus the enumerations, constants and error types they use. A closed set of
named values is a `StrEnum` (`Band`, `Severity`, `Aspect`, `Verdict`, ...), whose members keep the values the stored data
uses, so JSON and `.clean-eval/` files read as before:
- `policy.py`: the risk model and the value types and bands every model uses;
- `rating.py`: the code read, its knowledge graph, rated modules and the heat map, a judgement, a change's risk;
- `review.py`: the PR review in CI, with a CI run's step outputs, secrets, agent task and folders;
- `retro.py`: the merged-PR history and the tuning proposed from it;
- `errors.py`: the error types.

`DomainHoldsDataModels` in `tests/test_architecture.py` checks the following.

- **Validators only.** Every invariant is a validator (`field_validator`, `model_validator`, or a constrained type from
  `policy.py`). A model's methods are only validators, computed fields, properties or `classmethod` constructors.
- **No module-level functions.** Procedures are business rules and belong in `clean_eval/use_cases/rules/`.
- **Imports:** only the domain, the computation-only stdlib modules in `PURE_IMPORTS`, and pydantic.
- **No I/O and no clock reads.** Dates are passed in.
- **No external tool's shapes.** A model describes the business, never a tool's JSON: the gateway maps gh's
  `headRefOid` onto `PullRequest.head_sha`, and a model needs no validator to unwrap a tool's nesting.
- **Errors** derive from `HeatmapError` (`errors.py`). Convert a pydantic `ValidationError` with
  `HeatmapError.from_validation` (or the subclass's).
- **Policy changes:** `policy.py` holds the risk model's defaults. A repository overrides them in
  `.clean-eval/risk-model.json`, so a new knob needs a default here and validation of its override. The contract is in
  `.claude/skills/risk-heat-map/references/rate.md`.
- **Tests** go in `tests/domain/test_<module>.py`. They build the models they need with the fixtures of
  `tests/conftest.py` (`facts`, `ratings`, `heatmap`, ...), customised through `request.param`.
