# clean-eval
Flexible, scalable, and explainable verification framework for agentic development to follow clean architecture best practices.

## Skills

Claude Code skills in `.claude/skills/` form a feedback loop:

| Skill | What it does | Reads / writes |
|---|---|---|
| `/risk-heat-map` | Rates every module on four aspects (Business Impact, Side Effects & Reversibility, Entropy, Unverified) from static analysis plus a levelling read-through, and draws the treemap in the section below. | code → README + `docs/risk-heatmap.svg` |
| `/pr-review [PR#\|branch]` | Reviews a change against `pr-review/review-rules.md`, with depth scaled by the most severe band it touches in the heat map of its base. Every finding cites a rule ID. | heat map + rules → review |
| `/eval [period]` | Replays merged PRs in a period, links later fixes/reverts to the PRs that caused them, measures how well risk scores and rules predicted them, and recommends changes to weights, thresholds and rules. | history → advice for model + rules |

Requires [uv](https://docs.astral.sh/uv/), which provides Python 3.15 and the locked dependencies, and git; an
authenticated `gh` for reviews by PR number, posting, and rule metrics in `/eval`.

### Code layout

The skills hold only instructions; the logic is the `clean_eval/` package, in Clean Architecture layers whose
dependencies point inward. The GitHub Action runs the same package: `action.yml` runs its `action` command.

| Layer | Holds | May import |
|---|---|---|
| `domain/` | data models (pydantic) and the enumerations of their closed value sets; every invariant is a validator | stdlib computation modules, pydantic |
| `use_cases/` | the business logic, in business terms only: `interfaces.py` (the abstract classes of every outside utility it uses), the use cases as functions over those interfaces, and `rules/`, the pure business rules | `domain` |
| `adaptor/` | the presenters (SVG treemap, Markdown summary); rendering only | `domain`, `use_cases` |
| `external/` | one gateway per external dependency (git, GitHub, GitHub Actions, Claude Code, the coverage report, files, the environment), holding all there is to know about it; the command line, which is also the composition root; and the agent's guard hook | everything |

Beside the layers, `clean_eval/settings.py` holds `Settings` (pydantic-settings): everything a run reads from its
environment variables, never from a `.env` file, since in CI the working directory is the checkout under review. Every
layer but the domain may import it, and only the command line builds it; `clean_eval action --help` lists the variables.

`tests/test_architecture.py` enforces the structure: imports point inward and are absolute, the inner layers do no I/O
and never read the clock, the domain holds only data models, each interface has one implementation exposing nothing
more, each constant has one home, only the gateways reach outside the process (bar `Settings`, which reads the
environment, and the command line's console), and the code style of `AGENTS.md` holds.

Run it as `uv run python -I clean_eval <rate|readme|report|assess|history> ...` from this checkout, or from another
repository as `uv run --frozen --no-config --project <clean-eval> python -I <clean-eval>/clean_eval ...`. `-I` keeps
the rated repository off `sys.path`, so none of its code can be imported, and `--no-config` keeps its uv settings out.
The GitHub Action runs `clean_eval action <skill>`, which runs the CI steps in-process; `prepare`, `finalize`, `post`,
`redact` and `guard` (the agent's hook) are commands of their own too.

### Per-repo configuration

A repository's own tuning in `.clean-eval/` takes precedence over the bundled defaults, and `/eval` writes the
recommendations it applies there:

- `.clean-eval/risk-model.json`: overrides of the risk policy (weights, saturations, standards, module splitting); its
  contract is in `.claude/skills/risk-heat-map/references/rate.md`.
- `.clean-eval/review-rules.md`: the layers, the tier policy and the rules.

CI reads both from the PR's base commit, so a PR can't relax the rules it is judged by.

## GitHub Actions

Use the skills from any repository's workflow through the composite action in this one:

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
| `risk-heat-map` | no (static scores; `use-claude: true` adds the levelling read-through) | `readme`, `diagram` | `readme-changed`, `result` |
| `pr-review` | yes | `pr` (defaults to the triggering PR), `post`, `fail-on: never\|request_changes\|blocker` | `verdict`, `max-tier`, `blockers`, `result` |
| `eval` | yes | `since`, `until`, `apply` | `auc`, `result` |

Common inputs: `model`, `max-turns`, `claude-code-version`, `extra-prompt`, `working-directory`, `github-token`. Every
run writes `report.md`, `result.json` and `claude.log` to `outputs.output-dir`, and adds its report to the job summary.
The action never commits: pair it with a create-PR step. Ready-to-copy workflows are in
[`examples/workflows/`](examples/workflows).

**Versions:** `@v1` follows the latest 1.x [release](https://github.com/RainrainWu/clean-eval/releases), so fixes and
features arrive without breaking changes. To change versions only on purpose, pin a release (`@v1.2.0`) or, safest,
its commit SHA (`@<sha> # v1.2.0`).

- **Credentials:** [keyless](#keyless-credentials) workload identity federation, `anthropic-api-key` or
  `claude-code-oauth-token`; for Bedrock or Vertex, set `CLAUDE_CODE_USE_BEDROCK=1` or `CLAUDE_CODE_USE_VERTEX=1` and
  the cloud credentials in the step's `env`.
- **Permissions:** `pull-requests: write` to post reviews, `contents: read` otherwise, and `id-token: write` to sign in
  keyless.
- **Forks:** trigger reviews on `pull_request`, never `pull_request_target`, and skip fork and Dependabot PRs (they get
  no secrets and no OIDC token); the example workflow shows how.

### Keyless credentials

Workload identity federation signs the agent in without a stored key: the job's GitHub OIDC token, which names the
repository, the event and the branch, is traded for a short-lived credential of an Anthropic service account, under a
federation rule that decides which jobs may. Register GitHub as an issuer
(`https://token.actions.githubusercontent.com`) and add a service account and a rule in the Claude Console
([Anthropic's guide](https://platform.claude.com/docs/en/manage-claude/wif-providers/github-actions)). Then give the job
`id-token: write`, and give the action the IDs, which aren't secret:

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write                  # lets the job request its OIDC token
steps:
  - uses: RainrainWu/clean-eval@v1
    with:
      skill: pr-review
      anthropic-federation-rule-id: ${{ vars.ANTHROPIC_PR_FEDERATION_RULE_ID }}
      anthropic-organization-id: ${{ vars.ANTHROPIC_ORGANIZATION_ID }}
      anthropic-service-account-id: ${{ vars.ANTHROPIC_SERVICE_ACCOUNT_ID }}
```

The action requests the job's token outside the agent's sandbox and, while the agent runs, writes a new one every four
minutes, since GitHub's tokens last five minutes and each serves one trade. The token goes to a file the agent can't
read, and the agent never gets the means to request more. Don't set an API key or OAuth token as well: Claude Code
would sign in with it instead, so the action refuses the mix.

**Scoping the rules.** A rule matches the token's subject (`sub`) and claims. Give each kind of job a rule of its own,
as narrow as the job allows:

| Rule for | `subject_prefix` | Who can sign in |
|---|---|---|
| PR reviews | `repo:OWNER/REPO:pull_request` | a run for any PR from a branch of the repository |
| Scheduled runs (`eval`; `risk-heat-map` with `use-claude`) | `repo:OWNER/REPO:ref:refs/heads/main` | only the workflows on the default branch |
| A job in a GitHub environment | `repo:OWNER/REPO:environment:NAME` | only the runs the environment's protection rules admit |

- **Exact subjects.** A `subject_prefix` matches exactly unless it ends in `*`; `repo:OWNER/REPO:*` admits every
  branch, event and environment of the repository.
- **Immutable IDs.** Add `claims` such as `{"repository_id": "…", "repository_owner_id": "…"}`, so that a repository
  renamed, or deleted and re-created under the same name, doesn't inherit the rule. Repositories created after
  July 15, 2026 use GitHub's immutable `sub` format (`repo:OWNER@ID/REPO@ID:…`) by default. Copy the subject from a
  token of your own ([actions-oidc-debugger](https://github.com/github/actions-oidc-debugger) prints one) rather than
  writing it from memory.
- **Who a PR rule trusts.** A `pull_request` run uses the workflow file as the PR's branch has it, so the rule trusts
  everyone who can push a branch: the same trust as a repository secret, without a long-lived key to leak. Fork PRs get
  no OIDC token under `pull_request`, unless the repository sends write tokens to fork workflows. To trust fewer
  people, run the review in an environment with required reviewers, and scope its rule to that environment.
- **A workspace of its own.** Point the rules at a service account and a workspace kept for clean-eval, with a spend
  limit, so that a misused credential can't reach other work. A rule for one workspace needs no
  `anthropic-workspace-id`; a rule for all workspaces needs it.

### Trust model

The agent reads untrusted text (PR diffs and descriptions, commit messages, comments), so in CI it only *proposes*;
deterministic code decides and acts:

1. **Precompute** (`clean_eval prepare`): the PR's diff, per-file history and risk (from the heat map of its base
   commit), and the `.clean-eval/` config **from the base commit**; for `eval`, the history (every PR rated at its
   base), the defect patches and one-knob sensitivity replays.
2. **Sandboxed agent** (`clean_eval/external/claude_code.py`): `claude --restricted --strict-mcp-config` (no shell,
   web or MCP, and no settings or hooks from the checkout), with a scrubbed environment (no `GH_TOKEN`, runner tokens
   or means to request an OIDC token) and stdin from `/dev/null`. It follows clean-eval's own skills, copied into its
   context, and takes no instructions from the checkout: no `CLAUDE.md`, `AGENTS.md`, `.claude/rules/` or skills,
   which a PR could edit to steer its own review. Restricted mode skips them (verified with Claude Code 2.1.328), and
   the agent's settings exclude them explicitly too (`CLAUDE_CODE_DISABLE_CLAUDE_MDS`, `claudeMdExcludes`, the
   AGENTS.md setting, and a deny rule for the Skill tool). File tools may read the checkout and the context, and write
   only to the agent's output folder (and `.clean-eval/` for `eval` with `apply: true`). Claude Code enforces this,
   and so, independently, does a PreToolUse hook (`clean_eval guard`) that resolves real paths, so symlinks and
   `.git/` are off-limits; a hook that fails for any reason denies. clean-eval itself is installed from its own
   `uv.lock` (hash-checked, `--no-config`, outside every checkout), and every Python call runs isolated (`python -I`),
   so a module a PR adds (a `json.py`, a `pydantic.py`) can't shadow the standard library or a dependency.
3. **Finalize** (`clean_eval finalize`): validates the agent's output, turns unknown rule IDs into `NEW`, raises each
   finding to at least its rule's severity, and derives the verdict, blocker count and tier itself. Secrets (the
   values of secret-looking variables, and well-known token formats) are redacted from everything that leaves the
   runner, and step outputs carry only enums and numbers. `eval` edits to `.clean-eval/` are kept only if they touch
   tunable keys and don't lower AUC on replay; anything else under `.clean-eval/` is restored.
4. **Post** (`clean_eval post`): one review, with inline comments only on lines of the diff, posted with less when
   GitHub refuses it; the action's earlier "changes requested" reviews are dismissed, so a fixed PR isn't left blocked.

## Development

```bash
uv sync                                                # Python 3.15 and the locked dependencies, in .venv
uv run pytest                                          # every test: fake gh/claude, no network
uv run pytest -m "not e2e"                             # skip the subprocess end-to-end tests (seconds, not ~30 s)
uv run pytest tests/use_cases/test_heatmap.py          # one module (::TestRate for one class, -k to filter)
uv run ruff check --fix .                              # lint: pycodestyle, pyflakes, isort's import order
uv run ruff format .                                   # format: Black's style, 120 columns
uv run ty check                                        # type check (every function of the package is annotated)
```

The tests mirror the package: `tests/<layer>/test_<module>.py` tests `clean_eval/<layer>/<module>.py`, under the same
dependency rule. They are pytest classes; fixtures live in the `conftest.py` beside the tests that share them, and a
test customises one through `request.param`. Test modules hold no module-level data: environments, files, histories
and payloads are fixtures too, named in `@pytest.mark.parametrize` with pytest-lazy-fixtures' `lf` (`lfc` for a
variant). External dependencies are MagicMocks injected into the code under test; the use cases' git and file system
answer through in-memory fakes that pass the same contract checks as the real gateways. The top-level tests span the
layers: the architecture and style rules, the action's command end to end, and the release check. The `tests`
workflow runs the suite, ruff and ty on every pull request and every push to `master`.

Coding agents: `AGENTS.md` at the root and in each layer folder (and `tests/`) holds the commands and the rules that
apply there; each folder's `CLAUDE.md` imports its `AGENTS.md` for Claude Code.

### Releasing

Releases are [semver](https://semver.org) tags on `master`, `vMAJOR.MINOR.PATCH`: MAJOR when the action's inputs or
outputs, the `.clean-eval/` files or the command line change incompatibly, MINOR for backward-compatible features,
PATCH for fixes.

1. In a PR, bump the version with `uv version --bump minor` (or `major`, `patch`; `uv version 1.3.0-rc.1` for a
   pre-release). It updates `pyproject.toml` and `uv.lock`.
2. Once it is merged, tag the merge commit and push the tag: `git tag v1.3.0 <merge commit> && git push origin v1.3.0`.
3. The `release` workflow checks the tag ([`.github/scripts/check_release.py`](.github/scripts/check_release.py):
   semver, on `master`, equal to the version in `pyproject.toml`, newer than every release), runs the tests at it,
   publishes the GitHub Release with generated notes and moves the major tag (`v1`) to it. A pre-release
   (`-alpha.N`, `-beta.N`, `-rc.N`) is published as one and leaves `v1` alone.

If a check fails, nothing is published: delete the tag (`git push origin :refs/tags/v1.3.0`), fix, and tag again.

## Risk Heat Map

<!-- risk-heat-map:start -->
<!-- risk-heat-map:fingerprint=801b34699227 -->

![Risk heat map of clean-eval at ee84f6c](docs/risk-heatmap.svg)

Tile area is module size (lines of code, tests excluded); colour is the overall risk of changing it. Heuristics from static analysis of `chore/readme-only-heat-map` at `ee84f6c` (2026-11-22), not a verdict. Regenerate with the `risk-heat-map` skill.

🟢 Low 0-24 · 🟡 Moderate 25-49 · 🟠 High 50-74 · 🔴 Critical 75-100
<!-- risk-heat-map:end -->

