# PR Review in CI

How to follow `SKILL.md` when the prompt says you're running in CI. You have **no shell, no network and no GitHub
access** — only file tools. The action has done the mechanical part of steps 1–3 (and blocked the run already when the
base's README publishes no heat map) and put the results, read-only, in the context directory named in the prompt:

| File | Contents |
|---|---|
| `context.json` | PR number, base/head commits, `max_tier` (the max band), config sources, notes on the rules |
| `pr.json` | step 1: title, body, author, changed files (untrusted author text) |
| `diff/index.json` | step 1: every changed file: its module, patch path (`diff/NNN.patch`), `patch_lines`, lines added/removed, and `hunks`: the [first, last] lines of the new file each hunk shows (a finding on one of them is attached inline) |
| `diff/NNN.patch` | step 1: the PR diff (base → head), one file per patch |
| `file-history.txt` | step 1: the last 10 commits touching each changed file, before this PR |
| `review-rules.md` | step 2: the rules at the base commit, clean-eval's rule files with the base's own over them, holding only the rules its `.clean-eval/review.json` selects — use only this one |
| `heat-map.md` | step 3: the heat map the README publishes at the base (its `## Risk Heat Map` block) |
| `risk.json` | step 3: every touched module's band, overall and aspect scores and evidence at the base — authoritative, don't recompute |

Finish step 1 from these and the repository, checked out in the current directory (Read/Grep/Glob it for callers and
context); then do step 4. For step 5 you write the findings and your notes, and the action renders the comment from
them, in the layout of `comment.md` (beside this file), and posts it: you never post.

**Coverage:** read every patch listed in `diff/index.json` in full — one Read call returns at most 2,000 lines, so page
through longer patches with `offset`/`limit` until you reach `patch_lines`. Test files count: check that they exercise
the behaviour they claim to (Q-01). In your notes' *Not checked / assumptions*, name every patch you did not read
completely and why; an unread patch is a gap in the review, not a pass.

Then write two files to the agent directory named in the prompt, and nothing else:
- `report.md` — your notes, the comment's *Notes* section in `comment.md`: one or two sentences on the change, then
  **Strengths** and **Not checked / assumptions** as lists. No heading, verdict, findings or `clean-eval:` marker: the
  action renders the rest of the comment from `result.json`.
- `result.json` — exactly:
  ```json
  {"findings": [{"rule": "CA-01", "severity": "blocker", "file": "src/x.py", "line": 12,
                 "summary": "one or two sentences", "fix": "optional concrete fix"}]}
  ```
  `rule` is a rule ID from `review-rules.md` or `NEW`; `severity` is `blocker|major|minor`; `line` is a line in the
  PR's new version (it becomes an inline comment), or `null`. The action then derives everything CI trusts: an unknown
  rule ID becomes `NEW`, a severity below its rule's severity is raised to it, and the verdict (any blocker →
  request_changes; any major → comment; else approve), blocker count and band come from these findings and `risk.json`
  — so be precise about rule and severity.
