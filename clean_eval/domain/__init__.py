"""Domain layer: data models only. Pydantic models of the policy, the code read, the ratings, a change's risk, reviews,
the merged-PR history and an eval's outcome; every invariant is a validator, and nothing here is a procedure (those are
the business rules, in use_cases/rules). Pure: imports only computation modules and pydantic, and does no I/O."""
