"""The commands the GitHub Action (action.sh) runs around the CI agent. The agent only *proposes*; these decide and act:

prepare pr-review|eval   precompute what the agent reads, so it runs with no shell, no network and no token
finalize --skill S       validate what it proposed and write the result CI trusts, redacted, plus the step outputs
post                     post the validated review on the PR
redact FILE              scrub secrets from a file in place (e.g. the agent transcript)
guard                    the agent's PreToolUse hook: allow or deny one tool call
"""

import sys

from clean_eval.domain.errors import InvalidOutput
from clean_eval.external import agent_guard, wiring
from clean_eval.external.wiring import CLOCK, ENV, FS
from clean_eval.use_cases.finalize_run import SKILLS, finalize_run
from clean_eval.use_cases.post_review import post_review
from clean_eval.use_cases.prepare_eval import prepare_eval
from clean_eval.use_cases.prepare_review import prepare_review
from clean_eval.use_cases.rules.redaction import redact as redact_text
from clean_eval.use_cases.store import load_policy, write_step_outputs


def prepare(args):
    repo = wiring.repository_root(args.repo)
    FS.make_dirs(args.ctx)
    if args.skill == "pr-review":
        context = prepare_review(
            wiring.github(), wiring.git(repo), FS, CLOCK, args.ctx, args.pr, wiring.default_rules()
        )
    else:
        policy, source = load_policy(FS, repo)
        context = prepare_eval(
            wiring.git(repo),
            FS,
            CLOCK,
            args.ctx,
            repo,
            policy,
            source,
            wiring.period(args.since, args.until),
            wiring.default_rules(),
            host=wiring.github() if args.with_gh else None,
            branch=args.branch,
        )
    print(context.model_dump_json())


def finalize(args):
    try:
        result, outputs = finalize_run(FS, ENV, args.ctx, args.agent, args.out, args.skill, args.apply, args.workspace)
    except InvalidOutput as e:
        print("::error::clean-eval: invalid agent output: %s" % e, file=sys.stderr)
        return 2
    if result is not None and result.redactions:
        print(
            "::warning::clean-eval redacted %d secret-looking value(s) from agent output" % result.redactions,
            file=sys.stderr,
        )
    write_step_outputs(FS, args.gh_output, outputs)
    print(outputs.model_dump_json())


def post(args):
    outcome = post_review(wiring.github(), FS, ENV, args.out, args.pr, args.head, args.ctx)
    for level, message in outcome.log:
        if level == "warning":
            print("::warning::" + message, file=sys.stderr)
        else:
            print(message)
    return 0 if outcome.posted else 1


def redact(args):
    if FS.exists(args.path):
        text, n = redact_text(FS.read_text(args.path), wiring.secrets())
        if n:
            FS.write_text(args.path, text)
            print(
                "::warning::clean-eval redacted %d secret-looking value(s) from %s" % (n, FS.name(args.path)),
                file=sys.stderr,
            )


def guard(args):
    return agent_guard.hook()


def register(sub):
    p = sub.add_parser("prepare", help="CI: precompute the agent's context")
    kinds = p.add_subparsers(dest="skill", required=True)
    for skill in ("pr-review", "eval"):
        k = kinds.add_parser(skill)
        k.add_argument("--repo", default=".", help="repository root (default: current folder)")
        k.add_argument("--ctx", required=True, help="folder for the context")
        if skill == "pr-review":
            k.add_argument("--pr", type=int, required=True)
        else:
            k.add_argument("--since", required=True, help="YYYY-MM-DD")
            k.add_argument("--until", required=True, help="YYYY-MM-DD")
            k.add_argument("--with-gh", action="store_true", help="fetch review comments with gh")
            k.add_argument("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
    p.set_defaults(run=prepare)

    p = sub.add_parser("finalize", help="CI: validate the agent's output and write the result CI trusts")
    p.add_argument("--skill", choices=SKILLS, required=True)
    p.add_argument("--ctx", required=True)
    p.add_argument("--agent", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--workspace", default=".")
    p.add_argument("--apply", action="store_true", help="eval: keep the edits of .clean-eval/ that pass validation")
    p.add_argument("--gh-output", required=True, help="the step-outputs file ($GITHUB_OUTPUT)")
    p.set_defaults(run=finalize)

    p = sub.add_parser("post", help="CI: post the validated review on the PR")
    p.add_argument("--pr", required=True)
    p.add_argument("--ctx", help="context folder with diff/index.json, to attach only comments on diff lines")
    p.add_argument("--out", required=True)
    p.add_argument("--head", required=True)
    p.set_defaults(run=post)

    p = sub.add_parser("redact", help="CI: scrub secrets from a file in place")
    p.add_argument("path")
    p.set_defaults(run=redact)

    p = sub.add_parser("guard", help="CI: the agent's PreToolUse hook (tool call on stdin; exit 0 allow, 2 deny)")
    p.set_defaults(run=guard)
