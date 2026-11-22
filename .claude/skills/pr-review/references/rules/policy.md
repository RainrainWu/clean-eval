# Review Rules

Rules the `pr-review` skill applies. Every finding cites exactly one rule ID so `/retro` can measure
which rules catch real problems and which are noise. Edit freely; `/retro` proposes changes with evidence.

The rules of each category are in a file of their own, read after this one: `architecture.md`, `data.md`,
`quality.md` and `risk.md`. A repository's `.clean-eval/rules/<name>.md` replaces the file of that name or adds
one, and its `.clean-eval/review.json` can narrow which rules apply.

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
