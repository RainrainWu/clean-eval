# clean-eval
Flexible, scalable, and explainable verification framework for agentic development to follow clean architecture best practices.

## Skills

Claude Code skills in `.claude/skills/` form a feedback loop:

| Skill | What it does | Reads / writes |
|---|---|---|
| `/risk-heat-map` | Rates every module on four aspects (Business Impact, Side Effects & Reversibility, Entropy, Unverified) from static analysis plus a levelling read-through, and draws the treemap in the section below. | code → README + `docs/risk-heatmap.svg` |
| `/pr-review [PR#\|branch]` | Reviews a change against `pr-review/review-rules.md`, with depth scaled by the most severe band it touches in the heat map of its base. Every finding cites a rule ID. | heat map + rules → review |
| `/eval [period]` | Replays merged PRs in a period, links later fixes/reverts to the PRs that caused them, measures how well risk scores and rules predicted them, and recommends changes to weights, thresholds and rules. | history → advice for model + rules |

Requires Python 3 and git; `gh` (authenticated) for PR-number reviews, posting, and rule metrics in `/eval`.
Optional: the `code-review-graph` CLI, for call and execution-flow data in the heat map (`--graph code-review-graph`).

### Code layout

The skills hold only instructions; the logic is the `clean_eval/` package (Python 3 stdlib only), in Clean
Architecture layers whose dependencies point inward (enforced by `tests/test_architecture.py`):

| Layer | Holds | May import |
|---|---|---|
| `clean_eval/domain/` | entities, the risk policy, the four aspect rules, scoring, judgement, change assessment, README convention | nothing outside the stdlib's pure modules |
| `clean_eval/use_cases/` | rate a repository, assess a change, update the README, render; and the ports they need | `domain` |
| `clean_eval/adaptor/` | gateways (git, `ast` graph, code-review-graph, files) and presenters (SVG, HTML, Markdown) | `domain`, `use_cases` |
| `clean_eval/external/` | the command line and the composition root | everything |

Run it as `python3 -I clean_eval <rate|render|diagram|readme|report|assess> ...` (`-I` keeps the rated repository off
`sys.path`, so none of its code can be imported).

### Per-repo configuration

Each repo can keep its own tuning in `.clean-eval/` — these take precedence over the bundled defaults, and
`/eval` writes its applied recommendations there:

- `.clean-eval/risk-model.json` — overrides of the risk policy (weights, saturations, standards, module splitting);
  contract in `.claude/skills/risk-heat-map/references/rate.md`
- `.clean-eval/review-rules.md` — layers, tier policy, rules

## GitHub Actions

Use the skills from any repo's workflow via the composite action in this repo:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0                 # required: risk scoring reads git history
    persist-credentials: false     # recommended: keep the token out of .git/config
- uses: RainrainWu/clean-eval@v1
  with:
    skill: pr-review               # risk-heat-map | pr-review | eval
    anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
```

| Skill | Needs Claude credentials | Key inputs | Outputs |
|---|---|---|---|
| `risk-heat-map` | no (static scores; `use-claude: true` adds the levelling read-through) | `readme`, `diagram` | `readme-changed`, `page`, `result` |
| `pr-review` | yes | `pr` (defaults to the triggering PR), `post`, `fail-on: never\|request_changes\|blocker` | `verdict`, `max-tier`, `blockers`, `result` |
| `eval` | yes | `since`, `until`, `apply` | `auc`, `result` |

Common inputs: `model`, `max-turns`, `claude-code-version`, `extra-prompt`, `working-directory`, `github-token`.
Every run writes `report.md`, `result.json` and `claude.log` to `outputs.output-dir` and a job summary.
The action never commits — pair it with a create-PR step. Ready-to-copy workflows: [`examples/workflows/`](examples/workflows).

- **Credentials:** `anthropic-api-key` or `claude-code-oauth-token`; for Bedrock/Vertex set
  `CLAUDE_CODE_USE_BEDROCK=1` / `CLAUDE_CODE_USE_VERTEX=1` and cloud credentials in the step's `env`.
- **Permissions:** `pull-requests: write` to post reviews; `contents: read` otherwise.
- **Forks:** trigger reviews on `pull_request`, never `pull_request_target`, and skip fork/Dependabot PRs
  (they get no secrets) — see the example workflow.

### Trust model

The agent reads untrusted text (PR diffs and descriptions, commit messages, comments), so in CI it only
*proposes*; deterministic code decides and acts:

1. **Precompute** (`ci/prepare.py`): the PR diff, per-file history, the change's risk from the heat map of its
   base commit, and the `.clean-eval/` config **from the base commit** (a PR can't relax the rules it's judged by);
   for `eval`, the history (every PR rated against its base), defect patches and one-knob sensitivity replays.
2. **Sandboxed agent** (`ci/run.sh`): `claude --restricted --strict-mcp-config` — no shell, no web, no MCP, and
   no user/project settings or hooks from the checkout — with a scrubbed environment (no `GH_TOKEN` or runner
   tokens) and stdin from `/dev/null`. File tools are confined to the checkout and the precomputed context
   (read) and the agent's output folder (write; plus `.clean-eval/` for `eval` with `apply: true`), enforced
   both by Claude Code and independently by a PreToolUse hook (`ci/guard.py`) that resolves real paths, so
   symlinks and `.git/` are off-limits.
3. **Finalize** (`ci/result.py`): validates the agent's output, turns unknown rule IDs into `NEW`, raises each
   finding to at least its rule's severity, and derives the verdict, blocker count and tier itself. Secrets
   (values of secret-looking env vars and known token formats) are redacted from everything that leaves the
   runner; step outputs carry only enums and numbers. `eval` edits to `.clean-eval/` are kept only if they
   touch tunable keys and don't lower AUC on replay.
4. **Post**: one review with inline comments; earlier "changes requested" reviews from the action are
   dismissed so a fixed PR isn't left blocked.

## Development

```bash
python3 -m unittest discover -s tests      # stdlib only; fake gh/claude, no network
```

## Risk Heat Map

<!-- risk-heat-map:start -->
<!-- risk-heat-map:fingerprint=e8f28bb546fd -->

![Risk heat map of clean-eval at ab97855](docs/risk-heatmap.svg)

Tile area is module size (lines of code, tests excluded); colour is the overall risk of changing it. Heuristics from static analysis of `feat/risk-heatmap-clean-arch` at `ab97855` (2026-11-22), not a verdict. Regenerate with the `risk-heat-map` skill.

🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100
<!-- risk-heat-map:end -->

