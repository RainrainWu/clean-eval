## Data

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| D-01 | blocker | Low | **Consistent schemas.** A data shape agrees everywhere it crosses (layers, pipeline steps, producers and consumers), so nothing fails or corrupts data at runtime. |
| D-02 | major | Low | **Validated input.** Data from outside the process (requests, files, messages, agent output) is validated against a schema (e.g. pydantic) at the boundary, before it is used. |
| D-03 | blocker | Low | **No data drift.** A mapping between layers or formats keeps every value: no field dropped by accident, no unintended unit or type change, no lossy conversion. |
| D-04 | minor | Moderate | **Model hierarchy.** Shared fields live on a common base, and variants of one thing derive from the same ancestor. |
| D-05 | major | Moderate | **Schema fit.** Storage schemas, indexes and mappings fit how the data is queried: nothing a query needs is missing, and nothing no query uses is indexed. |
| D-06 | minor | Moderate | **Query cost.** Queries and query templates avoid needless cost: full scans, unbounded results, repeated round trips. |
