# clean_eval/domain

Data models only: pydantic models of the policy, the code read, the ratings and heat map, a change's risk, reviews, the
merged-PR history and an eval's outcome, plus the constants and error types they use. `DomainHoldsDataModels` in
`tests/test_architecture.py` checks the following.

- **Validators only.** Every invariant is a validator (`field_validator`, `model_validator`, or a constrained type from
  `values.py`). A model's methods are only validators, computed fields, properties or `classmethod` constructors.
- **No module-level functions.** Procedures are business rules and belong in `clean_eval/use_cases/rules/`.
- **Imports:** only the domain, the computation-only stdlib modules in `PURE_IMPORTS`, and pydantic.
- **No I/O and no clock reads.** Dates are passed in.
- **Errors** derive from `HeatmapError` (`errors.py`). Convert a pydantic `ValidationError` with
  `HeatmapError.from_validation` (or the subclass's).
- **Policy changes:** `policy.py` holds the risk model's defaults. A repository overrides them in
  `.clean-eval/risk-model.json`, so a new knob needs a default here and validation of its override. The contract is in
  `.claude/skills/risk-heat-map/references/rate.md`.
- **Tests** go in `tests/domain/test_<module>.py`. Build models there with `tests/domain/builders.py`.
