"""The commands the GitHub Action (action.sh) runs around the CI agent. The agent only *proposes*; these decide and act:

  prepare pr-review|eval   precompute what the agent reads, so it runs with no shell, no network and no token
  finalize --skill S       validate what it proposed and write the result CI trusts, redacted, plus the step outputs
  post                     post the validated review on the PR
  redact FILE              scrub secrets from a file in place (e.g. the agent transcript)
  guard                    the agent's PreToolUse hook: allow or deny one tool call
"""
import os
import sys

from ..adaptor.gateways.files import append_step_outputs, load_policy, read_text, write_text
from ..adaptor.gateways.git_cli import Git
from ..domain.errors import InvalidOutput
from ..use_cases.finalize_run import SKILLS
from . import agent_guard, wiring


def prepare(args):
    repo = Git(args.repo).toplevel()
    os.makedirs(args.ctx, exist_ok=True)
    if args.skill == "pr-review":
        context = wiring.prepare_review(repo, args.ctx).execute(args.pr)
    else:
        policy, source = load_policy(repo)
        context = wiring.prepare_eval(repo, args.ctx, args.with_gh).execute(
            policy, source, wiring.period(args.since, args.until), args.branch)
    print(context.model_dump_json())


def finalize(args):
    try:
        result, outputs = wiring.finalize_run(args.ctx, args.agent, args.out, args.workspace).execute(args.skill, args.apply)
    except InvalidOutput as e:
        print("::error::clean-eval: invalid agent output: %s" % e, file=sys.stderr)
        return 2
    if result is not None and result.redactions:
        print("::warning::clean-eval redacted %d secret-looking value(s) from agent output" % result.redactions,
              file=sys.stderr)
    append_step_outputs(args.gh_output, outputs)
    print(outputs.model_dump_json())


def post(args):
    outcome = wiring.post_review(args.out, args.ctx).execute(args.pr, args.head)
    for level, message in outcome.log:
        if level == "warning":
            print("::warning::" + message, file=sys.stderr)
        else:
            print(message)
    return 0 if outcome.posted else 1


def redact(args):
    if os.path.exists(args.path):
        text, n = wiring.redactor().redact(read_text(args.path))
        if n:
            write_text(args.path, text)
            print("::warning::clean-eval redacted %d secret-looking value(s) from %s" % (n, os.path.basename(args.path)),
                  file=sys.stderr)


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
