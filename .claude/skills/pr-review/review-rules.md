# Review Rules

Rules the `pr-review` skill applies. Every finding cites exactly one rule ID so `/eval` can measure
which rules catch real problems and which are noise. Edit freely; `/eval` proposes changes with evidence.

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
| interface | `**/adapters/**`, `**/controllers/**`, `**/api/**`, `**/handlers/**`, `**/presenters/**` |
| infrastructure | `**/infrastructure/**`, `**/infra/**`, `**/db/**`, `**/repositories/**`, `**/clients/**` |

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
| CA-03 | major | Moderate | **Ports over concretions.** Use cases depend on interfaces/protocols they own; concrete adapters are injected at the composition root, not constructed inside use cases. |
| CA-04 | major | Moderate | **Thin adapters.** Controllers/handlers/repositories translate and delegate; business decisions (branching on domain rules) belong in domain/application. |
| CA-05 | major | Moderate | **Module boundaries.** Cross-module access goes through the other module's public API/entrypoint, not its internals. New cross-module imports need justification. |
| CA-06 | minor | High | **Boundary models.** Persistence/transport schemas (ORM rows, request DTOs, SDK responses) are mapped to domain types at the boundary and don't leak inward. |

### Quality

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| Q-01 | major | Moderate | **Tests follow behaviour.** A behaviour change ships with a test that fails without it. In 🟠/🔴 modules this is a `blocker`. |
| Q-02 | major | Moderate | **Failure paths.** New I/O handles timeouts, retries/idempotency and partial failure explicitly; errors are not swallowed. |
| Q-03 | major | Low | **Contracts.** Changes to public APIs, schemas, events, or persisted formats are backward compatible or include a migration/versioning plan. |
| Q-04 | minor | Moderate | **Clarity.** Names, dead code, duplication and complexity — only when it materially hurts the reader. |

### Risk

| ID | Severity | Min tier | Rule |
|---|---|---|---|
| R-01 | major | High | **Blast radius.** PR touching a 🟠/🔴 module states impact and a rollback/feature-flag path in its description. |
| R-02 | major | High | **Scope.** A 🟠/🔴 PR that also mixes refactors with behaviour changes, or exceeds ~400 changed lines, should be split. |
| R-03 | blocker | Low | **Security.** No secrets, injection vectors, auth bypasses, or unsafe deserialization. |
