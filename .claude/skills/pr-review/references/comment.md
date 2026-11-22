# The review comment

Every review of a change is one comment in this layout, plus an inline comment on each finding that sits on a line of
the diff. In CI the action renders it: the judgement from the findings it trusts, and the notes from the agent's
`report.md`. Interactively, write it the same way.

## Layout

- **Heading:** `## clean-eval review: <verdict>`. Any blocker → Request changes; majors only → Approve with comments;
  else Approve.
- **Summary table**, one row: the risk (the max band and its score); the modules touched (the five riskiest, with their
  band and score, or unrated); how many rules the band reaches (those whose min tier is up to it) of all; and the
  findings by severity.
- **Rules violated:** a table with a row per finding: the rule (its ID and title), the severity, where it is
  (`<file>:<line>`), what is wrong (and anything the checks changed, in italics) and the fix. The rows are grouped by
  rule, the most severe rule first, then by ID. "None." when no rule is violated.
- **Not covered by a rule:** the findings tagged `NEW`, in a table of the same columns but the rule. "None." when there
  are none.
- **Table cells** are one line each, with any `|` escaped as `\|`; an empty cell is `—`.
- **Notes:** the reviewer's own words: one or two sentences on the change, then **Strengths** (at least one) and
  **Not checked / assumptions** (every patch not read in full among them), as lists. No verdict or findings here.
- **Inline comments:** `[<RULE-ID>][<severity>] <what is wrong>`, then `**Fix:** <the fix>`.
- **Marker:** the body ends with
  `<!-- clean-eval:pr-review tier=<max_tier> score=<max_score> rules=<IDs cited> rules_version=<8 chars> -->`, which
  `/retro` reads back to measure rule usefulness. In CI the action adds it.

## Example

```markdown
## clean-eval review: Request changes

| Risk | Modules | Rules checked | Findings |
|---|---|---|---|
| 🟠 High, score 62 | `src/payments` 🟠 62 · `src/shared` 🟢 12 · `infra` 🟡 unrated | 30 of 34 (min tier up to High) | 2 blocker · 1 major · 0 minor |

### Rules violated

| Rule | Severity | Where | Finding | Fix |
|---|---|---|---|---|
| **CA-01** Dependency rule | blocker | `src/payments/domain/invoice.py:12` | The domain imports `infra.db.session`, an outer layer. | Depend on an `InvoiceStore` port the use case owns. |
| **Q-01** Tests follow behaviour | blocker | `src/payments/api.py:40` | The new refund path has no test; a blocker, as the module is High. | Add a test that fails without the refund branch. |

### Not covered by a rule

| Severity | Where | Finding | Fix |
|---|---|---|---|
| major | `src/payments/api.py:52` | A failed payment is retried without a limit. | Cap the retries and surface the last error. |

### Notes

Adds refunds to payments: an API endpoint, and a retry when the provider fails.

**Strengths**
- The refund amount is validated at the boundary.

**Not checked / assumptions**
- `diff/004.patch` (a generated migration) was read only to line 2,000.
```
