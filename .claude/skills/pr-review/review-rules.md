# Review Rules

Rules the `pr-review` skill applies. Every finding cites exactly one rule ID so `/retro` can measure
which rules catch real problems and which are noise. Edit freely; `/retro` proposes changes with evidence.

Rule fields:
- **Severity** — `blocker` (must fix before merge), `major` (should fix), `minor` (nit / optional).
- **Min tier** — the lowest risk band at which the rule is checked. A PR is checked at the most severe band
  among the modules it touches, as rated by the Risk Heat Map at the PR's base (Low < Moderate < High < Critical;
  a path no rated module covers, such as a new module or non-Python code, counts as Moderate).

## Layers

Clean-architecture layers, innermost first. Dependencies may only point **inward** (down this list's
index). Map your repo's paths to layers here; files matching no layer are unclassified and exempt from
CA-01/CA-02.

| Layer | Path globs |
|---|---|
| domain | `**/domain/**`, `**/entities/**`, `**/models/**` |
| application | `**/application/**`, `**/usecases/**`, `**/use_cases/**`, `**/services/**` |
| interface | `**/adapters/**`, `**/adaptor/**`, `**/adaptors/**`, `**/gateways/**`, `**/controllers/**`, `**/api/**`, `**/handlers/**`, `**/presenters/**` |
| infrastructure | `**/infrastructure/**`, `**/infra/**`, `**/external/**`, `**/drivers/**`, `**/frameworks/**`, `**/db/**`, `**/repositories/**`, `**/clients/**` |

## Tier policy

| Max band touched | Depth |
|---|---|
| 🟢 Low | Rules with min tier Low. Skim; report only `blocker`/`major`. |
| 🟡 Moderate | Rules with min tier ≤ Moderate. Read every changed hunk. |
| 🟠 High | Rules with min tier ≤ High. Also read the callers/callees of changed public functions. |
| 🔴 Critical | All rules. Also trace each behaviour change end-to-end and state the rollback path. |

## Rules

### Architecture

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | **Dependency rule.** Code in an inner layer must not import from an outer layer (e.g. domain → infrastructure, application → interface). |
| CA-02 | major | Low | **Pure domain.** Domain code must not perform I/O, read env/config, call clocks/randomness directly, or depend on frameworks (ORM, HTTP, cloud SDKs). |
| CA-03 | major | Moderate | **Ports over concretions.** Use cases depend on interfaces/protocols they own and call the instance injected at the composition root; they never construct or import a concrete adapter. |
| CA-04 | major | Moderate | **Thin adapters.** Controllers, handlers and presenters translate and delegate; business decisions (branching on domain rules) belong in domain/application. |
| CA-05 | major | Moderate | **Module boundaries.** Cross-module access goes through the other module's public API/entrypoint, not its internals. New cross-module imports need justification. |
| CA-06 | minor | High | **Boundary models.** Persistence/transport schemas (ORM rows, request DTOs, SDK responses) are mapped to domain types at the boundary and don't leak inward. |
| CA-07 | blocker | Low | **Declared utilities.** Use cases reach every external utility (processes, files, network, SDKs, the clock, the environment) through an interface they own, never by a direct call, and know none of a tool's commands, formats or conventions. |
| CA-08 | blocker | Low | **Faithful implementations.** An implementation obeys its interface: the signature, and the results and errors its contract states. |
| CA-09 | blocker | Low | **Gateways only serve.** A gateway or repository wraps its tool (calls it, maps its data and errors) and decides nothing: business logic never lives there. |
| CA-10 | minor | Moderate | **Placement.** New files and modules follow the repository's existing structure and naming; one that fits nowhere says why. |

### Data

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| D-01 | blocker | Low | **Consistent schemas.** A data shape agrees everywhere it crosses (layers, pipeline steps, producers and consumers), so nothing fails or corrupts data at runtime. |
| D-02 | major | Low | **Validated input.** Data from outside the process (requests, files, messages, agent output) is validated against a schema (e.g. pydantic) at the boundary, before it is used. |
| D-03 | blocker | Low | **No data drift.** A mapping between layers or formats keeps every value: no field dropped by accident, no unintended unit or type change, no lossy conversion. |
| D-04 | minor | Moderate | **Model hierarchy.** Shared fields live on a common base, and variants of one thing derive from the same ancestor. |
| D-05 | major | Moderate | **Schema fit.** Storage schemas, indexes and mappings fit how the data is queried: nothing a query needs is missing, and nothing no query uses is indexed. |
| D-06 | minor | Moderate | **Query cost.** Queries and query templates avoid needless cost: full scans, unbounded results, repeated round trips. |

### Quality

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| Q-01 | major | Moderate | **Tests follow behaviour.** A behaviour change or a new critical path ships with a test that fails without it, and the tests it affects are updated; the finding names what to test. In 🟠/🔴 modules this is a `blocker`. |
| Q-02 | major | Moderate | **Failure paths.** Errors are handled where they can be: new I/O handles timeouts, retries/idempotency and partial failure, and exceptions are caught narrowly and never swallowed; the finding suggests the handling. |
| Q-03 | major | Low | **Contracts.** Changes to public APIs, schemas, events, or persisted formats are backward compatible or include a migration/versioning plan. |
| Q-04 | minor | Moderate | **Clarity.** Names (of models and fields too) are self-explanatory and can't be mistaken for similar ones; dead code and complexity are flagged when they materially hurt the reader. |
| Q-05 | blocker | Low | **No stray globals.** A module-level value that is really configuration or input is a setting or a parameter, not a global. |
| Q-06 | major | Moderate | **Function size.** A function over ~50 lines (tests aside) is split at its steps; the finding says where. |
| Q-07 | major | Moderate | **Parameter count.** A function taking more than 7 parameters groups related ones into a model; the finding proposes which. |
| Q-08 | major | Moderate | **Reuse.** A new helper doesn't duplicate one the repository already has; the finding names the one to reuse. |
| Q-09 | major | Moderate | **Concise docs.** Docstrings and comments are short and say what the code can't: one line where one will do, never a restatement of the code. |
| Q-10 | major | Moderate | **Meaningful tests.** No test only confirms that nothing happens where no logic exists. |
| Q-11 | major | Low | **Dependencies.** No dependency is added, or left behind, unused. |
| Q-12 | minor | Low | **Docs follow code.** The README and agent instructions (AGENTS.md, CLAUDE.md, skills) reflect the change. |

### Risk

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| R-01 | major | High | **Blast radius.** PR touching a 🟠/🔴 module states impact and a rollback/feature-flag path in its description. |
| R-02 | major | High | **Scope.** A 🟠/🔴 PR that mixes refactors with behaviour changes is split. |
| R-03 | blocker | Low | **Security.** No secrets, injection vectors, auth bypasses, or unsafe deserialization. |
| R-04 | blocker | Low | **Sensitive data.** No secret, credential or personal data is logged, printed, or put into errors, metrics or traces. |
| R-05 | major | Low | **Size.** More than ~25 files or ~500 lines of meaningful change (generated files aside), or ~400 lines in a 🟠/🔴 PR, is split into PRs a reviewer can hold in mind. |
| R-06 | major | Low | **Least privilege.** Permissions, roles, scopes and tokens the change grants are the minimum it needs. |
