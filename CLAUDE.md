# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Claude Code

The skills in `.claude/skills/` are both the product and usable on this repository itself. There is no `.clean-eval/`
here, so they use the bundled defaults:
- `/risk-heat-map` rewrites this README's `## Risk Heat Map` section and `docs/risk-heatmap.svg`.
- `/pr-review <PR#|branch>` reviews a change against `.claude/skills/pr-review/review-rules.md`.
- `/retro <period>` recommends tuning from the merged-PR history.

When editing a `SKILL.md`, keep its commands' literal `CE` prefix (`uv run --frozen --no-config --project ...`). The
skills tell the model to write commands with exactly that prefix.
