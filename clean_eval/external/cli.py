"""The command line: parse arguments, hand a use case the gateways and presenters this module builds (it is the
composition root), run it and print the outcome.

    uv run python -I <clean-eval checkout>/clean_eval <command> ...

  rate      rate every module of the working tree (or --rev) on the four aspects -> <out>/data.json
  readme    put the heat map into the repository's README section and its diagram file (no-op when unchanged)
  report    a short Markdown summary (top modules, what drives them)
  assess    the risk a change (base..head) carries, from the heat map of its base: what the PR review scales by
  history   merged PRs of a period, each rated at its base, with escaped defects and calibration (for the eval);
            --replay / --sensitivity re-score a collected history under candidate policies

The GitHub Action (action.sh) runs these around its agent, which only *proposes*; they decide and act:

  prepare pr-review|eval   precompute what the agent reads, so it runs with no shell, no network and no token
  finalize --skill S       validate what it proposed and write the result CI trusts, redacted, plus the step outputs
  post                     post the validated review on the PR
  redact FILE              scrub secrets from a file in place (e.g. the agent transcript)
  guard                    the agent's PreToolUse hook: allow or deny one tool call
"""

import argparse
import json
import sys
from typing import Optional

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.adaptor.presenters import DIAGRAM_PATH, MarkdownReportPresenter, SvgDiagramPresenter
from clean_eval.domain.errors import HeatmapError, InvalidOutput, InvalidRequest
from clean_eval.domain.evaluation import EvalHistory, Period
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Heatmap, Judgement, RatingCache
from clean_eval.external import agent_guard
from clean_eval.external.code_review_graph import CodeReviewGraphGateway
from clean_eval.external.environment import ClockGateway, EnvironmentGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.external.github import GitHubGateway
from clean_eval.use_cases.evaluation import collect_history, finalize_eval, prepare_eval
from clean_eval.use_cases.heatmap import (
    finalize_heatmap_report,
    rate_revision,
    rate_working_tree,
    summarize_heatmap,
    update_readme,
)
from clean_eval.use_cases.interfaces import CodeHost, DiagramPresenter, Git, KnowledgeGraph, ReportPresenter
from clean_eval.use_cases.review import assess_change, finalize_pr_review, post_review, prepare_review
from clean_eval.use_cases.rules.evaluation import replay, rules_warning, sensitivity
from clean_eval.use_cases.rules.review import redact as redact_text
from clean_eval.use_cases.rules.review import secret_values
from clean_eval.use_cases.store import (
    load_heatmap,
    load_judgement,
    load_policy,
    read_json,
    save_heatmap,
    write_step_outputs,
)

SKILLS = ("pr-review", "eval", "risk-heat-map")  # what `finalize --skill` accepts
GRAPHS = ("builtin", "code-review-graph")
# The stateless gateways, shared; git is per repository, gh per use.
FS, ENV, CLOCK = FileSystemGateway(), EnvironmentGateway(), ClockGateway()
CHECKOUT = FS.parent(FS.parent(FS.parent(FS.real_path(__file__))))  # the clean-eval checkout: package and skills
DEFAULT_RULES = FS.join(CHECKOUT, ".claude", "skills", "pr-review", "review-rules.md")


def git(repo: str) -> Git:
    return GitGateway(repo)


def github() -> CodeHost:
    return GitHubGateway()


def knowledge_graph() -> KnowledgeGraph:
    """The code-review-graph CLI: on PATH, else where its installer puts it."""
    return CodeReviewGraphGateway(
        FS.find_executable("code-review-graph", ENV.get("PATH"))
        or FS.join(ENV.home(), ".local", "bin", "code-review-graph")
    )


def diagram_presenter() -> DiagramPresenter:
    return SvgDiagramPresenter()


def report_presenter() -> ReportPresenter:
    return MarkdownReportPresenter()


def today() -> str:
    return CLOCK.today()


def secrets() -> tuple[str, ...]:
    """The values to redact: the secret-looking variables of this process's environment."""
    return secret_values(ENV.variables())


def default_rules() -> str:
    return FS.read_text(DEFAULT_RULES)


def repository_root(path: str) -> str:
    return GitGateway(path).toplevel()


def period(since: str, until: str, window_days: int = 30) -> Period:
    """The Period of PRs merged from `since` to `until` (YYYY-MM-DD, inclusive), with `window_days` of look-ahead."""
    try:
        return Period(since=since, until=until, window_days=window_days)
    except ValidationError as e:
        raise InvalidRequest.from_validation(e, "since/until must be dates as YYYY-MM-DD")


def prepare(args: argparse.Namespace) -> None:
    repo = repository_root(args.repo)
    FS.make_dirs(args.ctx)
    if args.skill == "pr-review":
        context = prepare_review(github(), git(repo), FS, CLOCK, args.ctx, args.pr, default_rules())
    else:
        policy, source = load_policy(FS, repo)
        context = prepare_eval(
            git(repo),
            FS,
            CLOCK,
            args.ctx,
            repo,
            policy,
            source,
            period(args.since, args.until),
            default_rules(),
            host=github() if args.with_gh else None,
            branch=args.branch,
        )
    print(context.model_dump_json())


def finalize(args: argparse.Namespace) -> Optional[int]:
    try:
        if args.skill == "pr-review":
            result, outputs = finalize_pr_review(FS, ENV, args.ctx, args.agent, args.out)
        elif args.skill == "eval":
            result, outputs = finalize_eval(FS, ENV, args.ctx, args.agent, args.out, args.apply, args.workspace)
        else:
            result, outputs = finalize_heatmap_report(FS, ENV, args.out)
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


def post(args: argparse.Namespace) -> int:
    outcome = post_review(github(), FS, ENV, args.out, args.pr, args.head, args.ctx)
    for level, message in outcome.log:
        if level == "warning":
            print("::warning::" + message, file=sys.stderr)
        else:
            print(message)
    return 0 if outcome.posted else 1


def redact(args: argparse.Namespace) -> None:
    if FS.exists(args.path):
        text, n = redact_text(FS.read_text(args.path), secrets())
        if n:
            FS.write_text(args.path, text)
            print(
                "::warning::clean-eval redacted %d secret-looking value(s) from %s" % (n, FS.name(args.path)),
                file=sys.stderr,
            )


def guard(args: argparse.Namespace) -> int:
    return agent_guard.hook()


def register(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--ctx", help="context folder with diff/index.json, to attach only comments on diff lines")
    p.add_argument("--out", required=True)
    p.add_argument("--head", required=True)
    p.set_defaults(run=post)

    p = sub.add_parser("redact", help="CI: scrub secrets from a file in place")
    p.add_argument("path")
    p.set_defaults(run=redact)

    p = sub.add_parser("guard", help="CI: the agent's PreToolUse hook (tool call on stdin; exit 0 allow, 2 deny)")
    p.set_defaults(run=guard)


def _policy(args: argparse.Namespace) -> tuple[Policy, str]:
    return load_policy(FS, args.repo, args.model)


def _loaded(args: argparse.Namespace) -> tuple[Heatmap, Optional[Judgement]]:
    judgement = load_judgement(FS, args.judgement) if getattr(args, "judgement", None) else None
    return load_heatmap(FS, args.data), judgement


def rate(args: argparse.Namespace) -> None:
    policy, source = _policy(args)
    if args.rev:
        if args.graph != "builtin":
            raise HeatmapError("--rev reads git objects; only --graph builtin works there")
        heatmap = rate_revision(git(args.repo), CLOCK, args.rev, policy, RatingCache(), args.depth, args.max_modules)
    elif args.graph == "code-review-graph":
        heatmap = rate_working_tree(
            git(args.repo),
            FS,
            CLOCK,
            policy,
            knowledge_graph(),
            FS.join(args.out, "graph"),
            args.depth,
            args.max_modules,
        )
    else:
        heatmap = rate_working_tree(git(args.repo), FS, CLOCK, policy, depth=args.depth, max_modules=args.max_modules)
    heatmap.notes.append("Policy: %s" % source)
    save_heatmap(FS, heatmap, FS.join(args.out, "data.json"))
    print(
        "%s @ %s (%s): %d modules, %d source lines -> %s"
        % (
            heatmap.repo,
            heatmap.ref,
            heatmap.sha,
            len(heatmap.modules),
            heatmap.loc_total,
            FS.join(args.out, "data.json"),
        )
    )
    for m in sorted(heatmap.modules, key=lambda m: -m.scores.overall)[:10]:
        s = m.scores
        print(
            "%-45s loc %-7d overall %-3d biz %-3d effects %-3d entropy %-3d unverified %-3s"
            % (m.name, m.loc, s.overall, s.biz, s.effects, s.entropy, "N/A" if s.unverified is None else s.unverified)
        )
    for note in heatmap.notes:
        print("note:", note)


def readme(args: argparse.Namespace) -> None:
    heatmap, judgement = _loaded(args)
    root = FS.absolute(args.repo)
    result = update_readme(
        FS,
        diagram_presenter(),
        FS.join(root, args.readme),
        FS.join(root, args.svg),
        heatmap,
        judgement,
        _policy(args)[0],
    )
    if args.status_json:
        FS.write_text(
            args.status_json,
            json.dumps(
                {
                    "readme_changed": result.changed,
                    "action": result.action,
                    "fingerprint": result.fingerprint,
                    "modules": len(result.heatmap.modules),
                }
            ),
        )
    if result.changed:
        verb = "added" if result.action == "ADD" else "updated"
        print("%s the heat map in %s and %s (%d modules)" % (verb, args.readme, args.svg, len(result.heatmap.modules)))
    else:
        print("unchanged: %s already shows these ratings (fingerprint %s)" % (args.readme, result.fingerprint))


def report(args: argparse.Namespace) -> None:
    heatmap, judgement = _loaded(args)
    final, text = summarize_heatmap(report_presenter(), heatmap, judgement, _policy(args)[0])
    if args.json_out:
        save_heatmap(FS, final, args.json_out)
    if args.out:
        FS.write_text(args.out, text)
    else:
        sys.stdout.write(text)


def assess(args: argparse.Namespace) -> None:
    policy, source = _policy(args)
    heatmap, risk = assess_change(git(args.repo), CLOCK, args.base, args.head, policy)
    out = risk.model_dump(mode="json")
    out.update(base=args.base, head=args.head, model_source=source, model_version=policy.version, notes=heatmap.notes)
    text = json.dumps(out, indent=1)
    if args.out:
        FS.write_text(args.out, text)
        print(
            "%s..%s: %s (max score %d) across %d modules -> %s"
            % (args.base[:8], args.head[:8], risk.max_band, risk.max_score, len(risk.modules), args.out)
        )
    else:
        print(text)


def history(args: argparse.Namespace) -> None:
    root = repository_root(args.repo)
    policy, source = load_policy(FS, root, args.model)
    if args.replay or args.sensitivity:
        if not args.from_history:
            raise InvalidRequest("--replay/--sensitivity need --from-history")
        try:
            collected = EvalHistory.model_validate(read_json(FS, args.from_history))
        except ValidationError as e:
            raise HeatmapError.from_validation(e, "%s is not a collected history" % args.from_history)
        out = (
            replay(collected, load_policy(FS, root, args.replay)[0]) if args.replay else sensitivity(collected, policy)
        )
    else:
        if not args.since:
            raise InvalidRequest("--since is required (or --from-history with --replay/--sensitivity)")
        out = collect_history(
            git(root),
            CLOCK,
            policy,
            period(args.since, args.until, args.defect_window_days),
            host=github() if args.with_gh else None,
            branch=args.branch,
            model_source=source,
            reviewer_logins=args.reviewer_login,
        )
        warning = rules_warning(out.rules) if out.rules else None
        if warning:
            print("warning: " + warning, file=sys.stderr)
    text = json.dumps(to_jsonable_python(out), indent=2)
    if not args.out:
        print(text)
        return
    FS.write_text(args.out, text)
    if isinstance(out, EvalHistory):
        print(
            "wrote %s: %d PRs, %d with escaped defects" % (args.out, out.totals.prs, out.totals.defective_prs),
            file=sys.stderr,
        )


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="clean_eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", default=".", help="repository root (default: current folder)")
        p.add_argument(
            "--model", help="risk-model overrides (default: <repo>/.clean-eval/risk-model.json when present)"
        )

    def data(p: argparse.ArgumentParser) -> None:
        p.add_argument("data", help="data.json from rate")
        p.add_argument("--judgement", help="optional judgement.json with the aspect levels")

    p = sub.add_parser("rate", help="rate every module on the four aspects")
    common(p)
    p.add_argument("--rev", help="rate this revision, read from git objects (default: the working tree)")
    p.add_argument(
        "--graph",
        default="builtin",
        choices=GRAPHS,
        help="builtin: imports and test links from ast; code-review-graph: calls and execution flows (must be installed)",
    )
    p.add_argument(
        "--depth", type=int, help="levels of modules: 1 is the top-level folders (default from the policy: 3)"
    )
    p.add_argument("--max-modules", type=int, help="at most this many modules (default from the policy: 60)")
    p.add_argument("--out", required=True, help="folder for data.json")
    p.set_defaults(run=rate)

    p = sub.add_parser("readme", help="put the heat map into the README section and the diagram file")
    common(p)
    data(p)
    p.add_argument("--readme", default="README.md", help="README path, relative to --repo")
    p.add_argument("--svg", default=DIAGRAM_PATH, help="diagram path, relative to --repo (default %s)" % DIAGRAM_PATH)
    p.add_argument("--status-json", help="write {readme_changed, action, fingerprint, modules} here")
    p.set_defaults(run=readme)

    p = sub.add_parser("report", help="a short Markdown summary")
    common(p)
    data(p)
    p.add_argument("--out", help="write here instead of stdout")
    p.add_argument("--json-out", help="also write the finalized heatmap (levels applied) as JSON")
    p.set_defaults(run=report)

    p = sub.add_parser("assess", help="the risk a change carries, from the heat map of its base")
    common(p)
    p.add_argument("--base", required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--out", help="write the JSON here instead of stdout")
    p.set_defaults(run=assess)

    p = sub.add_parser(
        "history", help="merged PRs of a period, rated at their bases, with escaped defects (for the eval)"
    )
    common(p)
    p.add_argument("--since", help="YYYY-MM-DD (required unless --from-history)")
    p.add_argument("--until", default=today(), help="YYYY-MM-DD (default today)")
    p.add_argument("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
    p.add_argument("--defect-window-days", type=int, default=30)
    p.add_argument("--with-gh", action="store_true", help="fetch review comments with gh (rule metrics)")
    p.add_argument(
        "--reviewer-login",
        action="append",
        help="login whose clean-eval markers count (repeatable; default: any [bot] login)",
    )
    p.add_argument("--from-history", help="reuse a collected history JSON instead of walking git")
    p.add_argument(
        "--replay", metavar="OVERRIDES_JSON", help="calibration of candidate risk-model overrides on --from-history"
    )
    p.add_argument("--sensitivity", action="store_true", help="one-knob replays of the policy on --from-history")
    p.add_argument("--out", help="write the JSON here instead of stdout")
    p.set_defaults(run=history)

    register(sub)
    return ap


def main(argv: Optional[list[str]] = None) -> None:
    args = parser().parse_args(argv)
    try:
        status = args.run(args)
    except HeatmapError as e:
        sys.exit("clean_eval: %s" % e)
    if status:
        sys.exit(status)
