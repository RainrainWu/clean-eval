"""The command line: parse arguments, wire adaptors into a use case, run it and print the outcome.

    python3 -I <clean-eval checkout>/clean_eval <command> ...

  rate     rate every module of the working tree (or --rev) on the four aspects -> <out>/data.json
  render   the interactive heat-map page (HTML)
  diagram  the README diagram (SVG treemap) and its README block, as files
  readme   put the heat map into the repository's README section and its diagram file (no-op when unchanged)
  report   a short Markdown summary (top modules, what drives them)
  assess   the risk a change (base..head) carries, from the heat map of its base: what the PR review scales by
"""
import argparse
import dataclasses
import json
import os
import sys
from pathlib import Path

from ..adaptor.gateways.files import FileReadmeStore, Workspace, load_policy, write_text
from ..adaptor.presenters.diagram import SvgDiagramPresenter
from ..adaptor.presenters.html_page import HtmlPagePresenter
from ..adaptor.presenters.markdown_report import MarkdownReportPresenter
from ..adaptor.presenters.style import DIAGRAM_PATH
from ..domain.errors import HeatmapError
from ..use_cases.present_heatmap import BLOCK_NAME, SVG_NAME, BuildDiagram, RenderPage, SummarizeHeatmap
from ..use_cases.update_readme import UpdateReadme
from . import wiring


def _policy(args):
    policy, source = load_policy(args.repo, args.model)
    return policy, source


def _loaded(args):
    ws = Workspace()
    judgement = ws.load_judgement(args.judgement) if getattr(args, "judgement", None) else None
    return ws.load_heatmap(args.data), judgement


def rate(args):
    policy, source = _policy(args)
    if args.rev:
        if args.graph != "builtin":
            raise HeatmapError("--rev reads git objects; only --graph builtin works there")
        rater = wiring.RevisionRaters(args.repo).at(args.rev, policy)
    else:
        rater = wiring.working_tree_rater(args.repo, policy, args.graph, os.path.join(args.out, "graph"))
    heatmap = rater.execute(policy, wiring.today(), args.depth, args.max_modules)
    heatmap.notes.append("Policy: %s" % source)
    Workspace().save_heatmap(heatmap, os.path.join(args.out, "data.json"))
    print("%s @ %s (%s): %d modules, %d source lines -> %s"
          % (heatmap.repo, heatmap.ref, heatmap.sha, len(heatmap.modules), heatmap.loc_total, os.path.join(args.out, "data.json")))
    for m in sorted(heatmap.modules, key=lambda m: -m.scores["overall"])[:10]:
        s = m.scores
        print("%-45s loc %-7d overall %-3d biz %-3d effects %-3d entropy %-3d unverified %-3s"
              % (m.name, m.loc, s["overall"], s["biz"], s["effects"], s["entropy"], "N/A" if s["unverified"] is None else s["unverified"]))
    for note in heatmap.notes:
        print("note:", note)


def render(args):
    heatmap, judgement = _loaded(args)
    template = Path(wiring.TEMPLATE).read_text()
    page = RenderPage(HtmlPagePresenter(template)).execute(heatmap, judgement, _policy(args)[0], args.suffix)
    write_text(args.out, page.html)
    print("wrote %s (%d bytes), %d modules, %d with judgement" % (args.out, len(page.html), len(page.heatmap.modules), page.heatmap.judged_modules))


def diagram(args):
    heatmap, judgement = _loaded(args)
    built = BuildDiagram(SvgDiagramPresenter()).execute(heatmap, judgement, _policy(args)[0], args.image_link)
    write_text(os.path.join(args.out_dir, SVG_NAME), built.svg)
    write_text(os.path.join(args.out_dir, BLOCK_NAME), built.readme_block)
    print("wrote %s and %s (%d modules)" % (os.path.join(args.out_dir, SVG_NAME), os.path.join(args.out_dir, BLOCK_NAME), len(built.heatmap.modules)))


def readme(args):
    heatmap, judgement = _loaded(args)
    root = os.path.abspath(args.repo)
    store = FileReadmeStore(os.path.join(root, args.readme), os.path.join(root, args.svg))
    result = UpdateReadme(SvgDiagramPresenter(), store).execute(heatmap, judgement, _policy(args)[0])
    if args.status_json:
        write_text(args.status_json, json.dumps({"readme_changed": result.changed, "action": result.action,
                                                 "fingerprint": result.fingerprint, "modules": len(result.heatmap.modules)}))
    if result.changed:
        verb = {"ADD": "added", "UPDATE": "updated"}[result.action]
        print("%s the heat map in %s and %s (%d modules)" % (verb, args.readme, args.svg, len(result.heatmap.modules)))
    else:
        print("unchanged: %s already shows these ratings (fingerprint %s)" % (args.readme, result.fingerprint))


def report(args):
    heatmap, judgement = _loaded(args)
    final, text = SummarizeHeatmap(MarkdownReportPresenter()).execute(heatmap, judgement, _policy(args)[0])
    if args.json_out:
        Workspace().save_heatmap(final, args.json_out)
    if args.out:
        write_text(args.out, text)
    else:
        sys.stdout.write(text)


def assess(args):
    policy, source = _policy(args)
    heatmap, risk = wiring.assess_change(args.repo, args.base, args.head, policy)
    out = dataclasses.asdict(risk)
    out.update(base=args.base, head=args.head, model_source=source, model_version=policy.version, notes=heatmap.notes)
    text = json.dumps(out, indent=1)
    if args.out:
        write_text(args.out, text)
        print("%s..%s: %s (max score %d) across %d modules -> %s" % (args.base[:8], args.head[:8], risk.max_band, risk.max_score,
                                                                   len(risk.modules), args.out))
    else:
        print(text)


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

    p = sub.add_parser("render", help="the interactive heat-map page")
    common(p)
    data(p)
    p.add_argument("--suffix", default="", help="appended to the page title, to tell variants apart")
    p.add_argument("--out", required=True)
    p.set_defaults(run=render)

    p = sub.add_parser("diagram", help="the README diagram (SVG) and its README block, as files")
    common(p)
    data(p)
    p.add_argument("--image-link", default=DIAGRAM_PATH, help="link to the SVG from the README (default %s)" % DIAGRAM_PATH)
    p.add_argument("--out-dir", required=True, help="receives %s and %s" % (SVG_NAME, BLOCK_NAME))
    p.set_defaults(run=diagram)

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
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        args.run(args)
    except HeatmapError as e:
        sys.exit("clean_eval: %s" % e)
