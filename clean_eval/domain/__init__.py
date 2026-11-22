"""Domain layer: data models only, in business terms (never an external tool's shapes). Pydantic models of the policy,
the code read, the ratings, a change's risk, reviews, a CI run, the merged-PR history and an eval's outcome, with the
enumerations of their closed value sets. Every invariant is a validator; procedures are business rules
(use_cases/rules). Pure: computation modules and pydantic only, no I/O."""
