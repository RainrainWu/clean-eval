# AGENTS.md

clean-eval checks that agent-written code follows clean architecture. It has three parts:
- the Claude Code skills in `.claude/skills/` (`/risk-heat-map`, `/pr-review`, `/eval`);
- a composite GitHub Action (`action.yml` → `action.sh`) that runs those skills in CI;
- the `clean_eval/` Python package, which holds all the logic.

The skills are instructions only. The skills, the action and people all run the package as one command line.

This file is the big picture. Each layer's own rules are in its `AGENTS.md`: `clean_eval/domain/`, `clean_eval/use_cases/`,
`clean_eval/adaptor/`, `clean_eval/external/` and `tests/`.

## Commands

```bash
uv sync                                                  # Python 3.15 and the locked dependencies (pydantic) in .venv
uv run pytest                                            # the whole suite, ~30 s; no network (fake gh and claude)
uv run pytest -m "not e2e"                               # without the subprocess end-to-end tests, ~5 s
uv run pytest tests/use_cases/test_heatmap.py            # one module (::test_helpers for one test, -k to filter)
uv run ruff check --fix .                                # lint: pycodestyle, pyflakes, isort's import order
uv run ruff format .                                     # format: Black's style, 120 columns
uv run python -I clean_eval rate --out <dir>             # rate this repository -> <dir>/data.json
uv run python -I clean_eval report <dir>/data.json       # the summary table
uv run python -I clean_eval assess --base master --head HEAD   # the risk a change carries
uv run python -I clean_eval history --since 2026-01-01 --until 2026-02-01   # merged PRs, rated at their bases
```

- **Other commands:** the action also runs `prepare`, `finalize`, `post`, `redact` and `guard`; see `clean_eval --help`.
- **From another repository:** `uv run --frozen --no-config --project <checkout> python -I <checkout>/clean_eval ...`.
- **Lint and format with ruff** before committing; CI fails on either check. Its settings are in `pyproject.toml`.
  `tests/test_architecture.py` enforces structure and imports.

## Architecture

The package is in four layers. Imports point inward only: domain ← use_cases ← adaptor ← external.

| Layer | Holds |
|---|---|
| `domain/` | pydantic data models only; every invariant is a validator |
| `use_cases/` | `interfaces.py` (the abstract classes of every external utility), plus module-level functions that take implementations of them: `heatmap.py`, `review.py`, `evaluation.py` and the shared `store.py`; `rules/` holds the pure business rules, by the same features plus `analysis.py` |
| `adaptor/` | the presenters (SVG treemap, Markdown summary) |
| `external/` | one gateway per external dependency, the command line `cli.py` (also the composition root) and the CI agent's guard `agent_guard.py` |

A command runs in this order:
1. `__main__.py` puts the checkout on `sys.path`.
2. A driver parses the arguments.
3. The driver builds the gateways and presenters (`cli.py` is the composition root) and calls a use-case function with them.
4. The driver prints the outcome.

The skills chain the commands:
- **Rating:** `rate` writes a `Heatmap` to `data.json`. An agent then levels the four aspects in `judgement.json`. Then
  `readme` and `report` render the result.
- **Change risk:** `assess` rates the PR's base commit and returns the risk band of each changed path. The PR review
  scales its depth by the worst band.
- **History:** `history` rates every merged PR at its base and links later fixes and reverts to it. It scores how well
  the risk predicted them, and `/eval` tunes `.clean-eval/` from that.

The action (`action.sh`) runs four steps. The agent only proposes; deterministic code decides:
1. `clean_eval prepare` precomputes the context: the diff, history, risk, and config read from the base commit.
2. A sandboxed `claude` runs with no shell, no network and no token. The `clean_eval guard` hook allows only listed
   paths and tools. It takes no instructions from the checkout: no CLAUDE.md, AGENTS.md, rules or skills.
3. `clean_eval finalize` validates the agent's output and derives the verdict itself.
4. `clean_eval post` posts the review.

`README.md` "Trust model" has the details.

## Rules that hold everywhere

- **The rated repository's code is data.** Read it only as text (`ast`, regexes, git objects); never import, install or
  run it.
- **Every Python call runs isolated, with `python -I`.** This applies in `action.sh`, the skills and the docs, so a module
  planted in a checkout (a `json.py`, a `pydantic.py`) can't shadow the standard library or a dependency.
- **Imports are absolute** (`from clean_eval.domain.policy import Policy`), never relative.
- **Logic goes into `clean_eval`; the skills hold only instructions.** They invoke the package as
  `uv run --frozen --no-config --project ${CLEAN_EVAL_HOME:-.} python -I ${CLEAN_EVAL_HOME:-.}/clean_eval`.
- **A repository's own tuning in `.clean-eval/` overrides the bundled defaults.**
  - `risk-model.json` overrides `domain/policy.py`; its contract is in `.claude/skills/risk-heat-map/references/rate.md`.
  - `review-rules.md` overrides `.claude/skills/pr-review/review-rules.md`.
  - CI reads both from the PR's base commit, so a PR can't relax the rules it is judged by.
- **Don't hand-edit generated output.** `clean_eval readme` writes the README's `## Risk Heat Map` section (between its
  markers) and `docs/risk-heatmap.svg`.
- **The action never commits.**
- **The CI agent takes no instructions from the checkout it reviews.** `action.sh` keeps `--restricted`, sets
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS`, and writes settings that exclude memory files, AGENTS.md, rules and skills.
  When you bump the action's `claude-code-version`, re-check with a canary checkout: plant tokens in each kind of file,
  log loads with an `InstructionsLoaded` hook, and confirm nothing loads.
- **Each `clean_eval` layer must stay in one rules layer.** The `## Layers` table in
  `.claude/skills/pr-review/review-rules.md` maps them, and `ReviewRulesCoverThisPackage` checks it.

## Releases

Releases are semver tags on `master`, and the version lives in `pyproject.toml`.
1. Bump the version in a PR with `uv version --bump minor` (or `major` / `patch`; `uv version X.Y.Z-rc.N` for a
   pre-release). It updates `uv.lock` too.
2. After it merges, tag the merge commit `vX.Y.Z` and push the tag.
3. `.github/workflows/release.yml` takes over:
   - checks the tag with `.github/scripts/check_release.py`;
   - runs the tests;
   - publishes the GitHub Release;
   - moves the major tag (`v1`) that action users pin.
