"""The command line: parse arguments, hand a use case the gateways and presenters from wiring.py, run it and print the
outcome.

    uv run python -I <clean-eval checkout>/clean_eval <command> ...

  rate      rate every module of the working tree (or --rev) on the four aspects -> <out>/data.json
  readme    put the heat map into the repository's README section and its diagram file (no-op when unchanged)
  report    a short Markdown summary (top modules, what drives them)
  assess    the risk a change (base..head) carries, from the heat map of its base: what the PR review scales by
  history   merged PRs of a period, each rated at its base, with escaped defects and calibration (for the eval);
            --replay / --sensitivity re-score a collected history under candidate policies

The GitHub Action also runs prepare, finalize, post, redact and guard around its agent (see ci.py).
"""
import argparse
import json
import sys

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.adaptor.presenters.style import DIAGRAM_PATH
from clean_eval.domain.errors import HeatmapError, InvalidRequest
from clean_eval.domain.history import EvalHistory
from clean_eval.domain.rating import RatingCache
from clean_eval.external import ci, wiring
from clean_eval.external.wiring import CLOCK, FS
from clean_eval.use_cases.assess_change import assess_change
from clean_eval.use_cases.collect_history import collect_history
from clean_eval.use_cases.rate_repository import rate_revision, rate_working_tree
from clean_eval.use_cases.rules.calibration import replay, sensitivity
from clean_eval.use_cases.rules.history import rules_warning
from clean_eval.use_cases.store import load_heatmap, load_judgement, load_policy, read_json, save_heatmap
from clean_eval.use_cases.summarize_heatmap import summarize_heatmap
from clean_eval.use_cases.update_readme import update_readme


def _policy(args):
    return load_policy(FS, args.repo, args.model)


def _loaded(args):
    judgement = load_judgement(FS, args.judgement) if getattr(args, "judgement", None) else None
    return load_heatmap(FS, args.data), judgement


def rate(args):
    policy, source = _policy(args)
    if args.rev:
        if args.graph != "builtin":
            raise HeatmapError("--rev reads git objects; only --graph builtin works there")
        heatmap = rate_revision(wiring.git(args.repo), CLOCK, args.rev, policy, RatingCache(), args.depth, args.max_modules)
    elif args.graph == "code-review-graph":
        heatmap = rate_working_tree(wiring.git(args.repo), FS, CLOCK, policy, wiring.knowledge_graph(),
                                    FS.join(args.out, "graph"), args.depth, args.max_modules)
    else:
        heatmap = rate_working_tree(wiring.git(args.repo), FS, CLOCK, policy, depth=args.depth, max_modules=args.max_modules)
    heatmap.notes.append("Policy: %s" % source)
    save_heatmap(FS, heatmap, FS.join(args.out, "data.json"))
    print("%s @ %s (%s): %d modules, %d source lines -> %s"
          % (heatmap.repo, heatmap.ref, heatmap.sha, len(heatmap.modules), heatmap.loc_total, FS.join(args.out, "data.json")))
    for m in sorted(heatmap.modules, key=lambda m: -m.scores.overall)[:10]:
        s = m.scores
        print("%-45s loc %-7d overall %-3d biz %-3d effects %-3d entropy %-3d unverified %-3s"
              % (m.name, m.loc, s.overall, s.biz, s.effects, s.entropy, "N/A" if s.unverified is None else s.unverified))
    for note in heatmap.notes:
        print("note:", note)


def readme(args):
    heatmap, judgement = _loaded(args)
    root = FS.absolute(args.repo)
    result = update_readme(FS, wiring.diagram_presenter(), FS.join(root, args.readme), FS.join(root, args.svg), heatmap,
                           judgement, _policy(args)[0])
    if args.status_json:
        FS.write_text(args.status_json, json.dumps({"readme_changed": result.changed, "action": result.action,
                                                 "fingerprint": result.fingerprint, "modules": len(result.heatmap.modules)}))
    if result.changed:
        verb = {"ADD": "added", "UPDATE": "updated"}[result.action]
        print("%s the heat map in %s and %s (%d modules)" % (verb, args.readme, args.svg, len(result.heatmap.modules)))
    else:
        print("unchanged: %s already shows these ratings (fingerprint %s)" % (args.readme, result.fingerprint))


def report(args):
    heatmap, judgement = _loaded(args)
    final, text = summarize_heatmap(wiring.report_presenter(), heatmap, judgement, _policy(args)[0])
    if args.json_out:
        save_heatmap(FS, final, args.json_out)
    if args.out:
        FS.write_text(args.out, text)
    else:
        sys.stdout.write(text)


def assess(args):
    policy, source = _policy(args)
    heatmap, risk = assess_change(wiring.git(args.repo), CLOCK, args.base, args.head, policy)
    out = risk.model_dump(mode="json")
    out.update(base=args.base, head=args.head, model_source=source, model_version=policy.version, notes=heatmap.notes)
    text = json.dumps(out, indent=1)
    if args.out:
        FS.write_text(args.out, text)
        print("%s..%s: %s (max score %d) across %d modules -> %s" % (args.base[:8], args.head[:8], risk.max_band, risk.max_score,
                                                                   len(risk.modules), args.out))
    else:
        print(text)


def history(args):
    root = wiring.repository_root(args.repo)
    policy, source = load_policy(FS, root, args.model)
    if args.replay or args.sensitivity:
        if not args.from_history:
            raise InvalidRequest("--replay/--sensitivity need --from-history")
        try:
            collected = EvalHistory.model_validate(read_json(FS, args.from_history))
        except ValidationError as e:
            raise HeatmapError.from_validation(e, "%s is not a collected history" % args.from_history)
        out = replay(collected, load_policy(FS, root, args.replay)[0]) if args.replay else sensitivity(collected, policy)
    else:
        if not args.since:
            raise InvalidRequest("--since is required (or --from-history with --replay/--sensitivity)")
        out = collect_history(wiring.git(root), CLOCK, policy, wiring.period(args.since, args.until, args.defect_window_days),
                              host=wiring.github() if args.with_gh else None, branch=args.branch, model_source=source,
                              reviewer_logins=args.reviewer_login)
        warning = rules_warning(out.rules) if out.rules else None
        if warning:
            print("warning: " + warning, file=sys.stderr)
    text = json.dumps(to_jsonable_python(out), indent=2)
    if not args.out:
        print(text)
        return
    FS.write_text(args.out, text)
    if isinstance(out, EvalHistory):
        print("wrote %s: %d PRs, %d with escaped defects" % (args.out, out.totals.prs, out.totals.defective_prs), file=sys.stderr)


def parser():
    ap = argparse.ArgumentParser(prog="clean_eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--repo", default=".", help="repository root (default: current folder)")
        p.add_argument("--model", help="risk-model overrides (default: <repo>/.clean-eval/risk-model.json when present)")

    def data(p):
        p.add_argument("data", help="data.json from rate")
        p.add_argument("--judgement", help="optional judgement.json with the aspect levels")

    p = sub.add_parser("rate", help="rate every module on the four aspects")
    common(p)
    p.add_argument("--rev", help="rate this revision, read from git objects (default: the working tree)")
    p.add_argument("--graph", default="builtin", choices=wiring.GRAPHS,
                   help="builtin: imports and test links from ast; code-review-graph: calls and execution flows (must be installed)")
    p.add_argument("--depth", type=int, help="levels of modules: 1 is the top-level folders (default from the policy: 3)")
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

    p = sub.add_parser("history", help="merged PRs of a period, rated at their bases, with escaped defects (for the eval)")
    common(p)
    p.add_argument("--since", help="YYYY-MM-DD (required unless --from-history)")
    p.add_argument("--until", default=wiring.today(), help="YYYY-MM-DD (default today)")
    p.add_argument("--branch", help="default: origin/HEAD, origin/main|master, or HEAD")
    p.add_argument("--defect-window-days", type=int, default=30)
    p.add_argument("--with-gh", action="store_true", help="fetch review comments with gh (rule metrics)")
    p.add_argument("--reviewer-login", action="append",
                   help="login whose clean-eval markers count (repeatable; default: any [bot] login)")
    p.add_argument("--from-history", help="reuse a collected history JSON instead of walking git")
    p.add_argument("--replay", metavar="OVERRIDES_JSON", help="calibration of candidate risk-model overrides on --from-history")
    p.add_argument("--sensitivity", action="store_true", help="one-knob replays of the policy on --from-history")
    p.add_argument("--out", help="write the JSON here instead of stdout")
    p.set_defaults(run=history)

    ci.register(sub)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        status = args.run(args)
    except HeatmapError as e:
        sys.exit("clean_eval: %s" % e)
    if status:
        sys.exit(status)
