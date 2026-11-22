"""Use cases of the risk heat map: rate a working tree or a revision (read through Git and the FileSystem, with the
built-in graph or a KnowledgeGraph), apply a reviewer's levels, summarize it, and put it into the README."""

import hashlib
import re
from collections import Counter

from clean_eval.domain.errors import HeatmapError, NoPythonSource
from clean_eval.domain.rating import Facts, Heatmap, ReadmeUpdate, Snapshot, SourceFile, TopFile
from clean_eval.domain.review import StepOutputs
from clean_eval.use_cases.rules import analysis
from clean_eval.use_cases.rules.analysis import (
    builtin_graph,
    dependents,
    file_signals,
    functions_by_module,
    is_test,
    split_modules,
    test_files_to_modules,
)
from clean_eval.use_cases.rules.heatmap import (
    apply_levels,
    block_fingerprint,
    fingerprint,
    merge_readme,
    rate_module,
    refresh_overall,
)
from clean_eval.use_cases.rules.review import secret_values
from clean_eval.use_cases.store import write_run_output

# The sources to rate, through the Git and FileSystem interfaces: the Python files of a working tree, or of any revision
# read straight from git objects. Files are only read as text; nothing from the repository is imported or run.

GENERATED = re.compile(r"(_pb2(_grpc)?\.py$|/generated/|/vendor/|/site-packages/)")
MAX_FILE_BYTES = 1_500_000


def count_loc(text):
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def _wanted(path):
    return path.endswith(".py") and not GENERATED.search("/" + path)


def content_id(text):
    """Same id as `git hash-object` for UTF-8 text, so working-tree and revision reads share caches."""
    data = text.encode("utf-8", "surrogateescape")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def working_tree_snapshot(git):
    sha = git.rev_parse("HEAD", short=True)
    return Snapshot(repo=git.repo_name(), ref=git.branch() or sha, sha=sha, root=git.toplevel())


def revision_snapshot(git, rev):
    return Snapshot(repo=git.repo_name(), ref=rev, sha=git.rev_parse(rev, short=True), root=git.toplevel())


def read_working_tree(git, fs, policy):
    """(Snapshot, the tracked Python files of the working tree as they are on disk now)."""
    snapshot, out = working_tree_snapshot(git), []
    for rel in git.ls_files():
        if not _wanted(rel):
            continue
        path = fs.join(snapshot.root, rel)
        # a symlink could point outside the repository; a tracked file may be gone from the working tree
        if fs.is_link(path) or not fs.is_file(path) or fs.size(path) > MAX_FILE_BYTES:
            continue
        try:
            text = fs.read_text(path)
        except HeatmapError:
            continue
        out.append(
            SourceFile(path=rel, text=text, loc=count_loc(text), is_test=is_test(rel, policy), blob=content_id(text))
        )
    return snapshot, out


def read_revision(git, rev, policy, blobs=None):
    """(Snapshot, the Python files of a revision), read from git objects (no checkout). `blobs` ({blob id: text}, e.g. a
    RatingCache's) is shared across revisions, so each file version is read once."""
    blobs = {} if blobs is None else blobs
    entries = [(p, b) for p, b, size in git.ls_tree(rev) if _wanted(p) and size <= MAX_FILE_BYTES]
    blobs.update(git.read_blobs([b for _, b in entries if b not in blobs]))
    files = [
        SourceFile(path=p, text=blobs[b], loc=count_loc(blobs[b]), is_test=is_test(p, policy), blob=b)
        for p, b in entries
        if b in blobs
    ]
    return revision_snapshot(git, rev), files


# Line coverage from a Cobertura coverage.xml in a working tree, when there is one (revisions have none), through the
# FileSystem interface.

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')


def coverage_line_rates(fs, root):
    """{file path: line rate 0-1} from the first coverage.xml at or below `root` (one level), or {}."""
    for report in fs.glob(root, "coverage.xml") + fs.glob(root, "*/coverage.xml"):
        if fs.is_link(report):
            continue
        return {m.group(1): float(m.group(2)) for m in CLASS.finditer(fs.read_text(report))}
    return {}


# Use cases: rate every sub-module of a repository on the four aspects. The working tree is read through the Git and
# FileSystem interfaces, with the built-in graph or a KnowledgeGraph (code-review-graph); a revision is read straight
# from git objects. The rating date comes from the Clock.


def rated_sources(files, policy):
    """The files the ratings cover: source (not tests) that the policy doesn't exclude."""
    return [f for f in files if not f.is_test and not analysis.excluded(f.path, policy)]


def _no_python(snapshot):
    return NoPythonSource("no Python source files in %s at %s" % (snapshot.repo, snapshot.ref))


def rate(snapshot, files, graph, policy, today, coverage=None, signals_cache=None, depth=None, max_modules=None):
    """The Heatmap of a snapshot's files, given their knowledge graph and coverage ({path: line rate})."""
    src = rated_sources(files, policy)
    mods, notes = split_modules(src, policy, depth, max_modules)
    if not mods:
        raise _no_python(snapshot)
    notes.insert(
        0,
        "Knowledge graph (%s): %d nodes, %d edges, %d flows"
        % (
            graph.stats.get("source", "graph"),
            graph.stats.get("nodes", 0),
            graph.stats.get("edges", 0),
            graph.stats.get("flows", 0),
        ),
    )
    coverage = coverage or {}
    file_mod = {f.path: m for m, fl in mods.items() for f in fl}
    fan_in, fan_out = dependents(graph, file_mod)
    funcs_of, test_mods = functions_by_module(graph, file_mod), test_files_to_modules(graph, file_mod)
    test_loc = {f.path: f.loc for f in files if f.is_test}
    total_crit = sum(c for c, _, _ in graph.flows) or 1
    total_loc = sum(f.loc for f in src) or 1
    signals = {f.path: file_signals(f, signals_cache) for fl in mods.values() for f in fl}

    def facts_of(name, mod_files):
        loc = sum(f.loc for f in mod_files)
        py = [signals[f.path].stats for f in mod_files if signals[f.path].stats]
        n = sum(len(s.funcs) for s in py)
        counts, examples = Counter(), {}
        for f in sorted(mod_files, key=lambda x: x.path):
            for kind, (count, line) in signals[f.path].effects.items():
                counts[kind] += count
                examples.setdefault(kind, "%s:%d" % (f.path, line))
        functions = funcs_of.get(name, [])
        linked = [q for q in functions if graph.tested.get(q)]
        test_files = set().union(*[graph.tested[q] for q in linked]) if linked else set()
        rates = [(coverage[f.path], f.loc) for f in mod_files if f.path in coverage]
        return Facts(
            name=name,
            loc=loc,
            files=len(mod_files),
            share=loc / total_loc,
            funcs=[x for s in py for x in s.funcs],
            typed_share=sum(s.typed for s in py) / n if n else None,
            broad=sum(s.broad for s in py),
            swallow=sum(s.swallow for s in py),
            todo=sum(signals[f.path].todo for f in mod_files),
            big_files=sum(1 for f in mod_files if f.loc > policy.big_file_lines),
            fan_in=len(fan_in[name]),
            fan_out=len(fan_out[name]),
            effect_counts=counts,
            effect_examples=examples,
            migrations=sum(1 for f in mod_files if analysis.is_migration(f.path, policy)),
            entry_flows=[crit for crit, entry, _ in graph.flows if file_mod.get(entry) == name],
            flow_touch=sum(crit for crit, _, fs in graph.flows if any(file_mod.get(x) == name for x in fs))
            / total_crit,
            keyword=analysis.has_core_keyword(name, policy),
            tooling=analysis.looks_like_tooling(name, mod_files[0].path, policy),
            graph_functions=len(functions),
            linked_functions=len(linked),
            linked_test_files=len(test_files),
            linked_test_loc=sum(test_loc.get(t, 0) / max(len(test_mods[t]), 1) for t in test_files),
            test_scales=Counter(analysis.classify_scale(t) for t in sorted(test_files)),
            line_rate=sum(r * w for r, w in rates) / max(sum(w for _, w in rates), 1) if rates else None,
            na_reason=analysis.not_applicable(name, policy),
            flows_available=graph.has_flows,
        )

    def top_files(mod_files):
        out = []
        for f in sorted(mod_files, key=lambda f: -f.loc)[:5]:
            stats = signals[f.path].stats
            out.append(
                TopFile(path=f.path, loc=f.loc, max_fn=max((x[0] for x in stats.funcs), default=0) if stats else None)
            )
        return out

    ratings = sorted(
        (rate_module(facts_of(name, fl), top_files(fl), policy) for name, fl in mods.items()), key=lambda r: -r.loc
    )
    notes.append("Unverified uses test links from the graph" + ("" if coverage else "; no coverage report in the repo"))
    return Heatmap(
        repo=snapshot.repo,
        ref=snapshot.ref,
        sha=snapshot.sha,
        generated_at=today,
        loc_total=total_loc,
        modules=ratings,
        notes=notes,
        policy_version=policy.version,
    )


def rate_working_tree(git, fs, clock, policy, knowledge_graph=None, graph_dir=None, depth=None, max_modules=None):
    """The Heatmap of the working tree as it is on disk. With a KnowledgeGraph, its graph (built into `graph_dir`, by
    default under .git) replaces the built-in one."""
    snapshot, files = read_working_tree(git, fs, policy)
    if not rated_sources(files, policy):
        raise _no_python(snapshot)
    if knowledge_graph is None:
        graph = builtin_graph(files)
    else:
        data_dir = graph_dir or fs.join(snapshot.root, ".git", "clean-eval-graph")
        fs.make_dirs(data_dir)
        graph = knowledge_graph.graph_of(snapshot.root, data_dir)
    return rate(
        snapshot,
        files,
        graph,
        policy,
        clock.today(),
        coverage_line_rates(fs, snapshot.root),
        depth=depth,
        max_modules=max_modules,
    )


def rate_revision(git, clock, rev, policy, cache, depth=None, max_modules=None):
    """The Heatmap of a revision, read from git objects. `cache` (a RatingCache) shares file contents and analysis
    across revisions, so rating many of them reads and parses each file version once."""
    snapshot, files = read_revision(git, rev, policy, cache.blobs)
    if not rated_sources(files, policy):
        raise _no_python(snapshot)
    return rate(
        snapshot,
        files,
        builtin_graph(files, cache.syntax),
        policy,
        clock.today(),
        signals_cache=cache.signals,
        depth=depth,
        max_modules=max_modules,
    )


# Use case: turn a rated heatmap into the one that is shown: apply the reviewer's levels (a Judgement, or None), then
# recompute the overall scores.


def finalize_heatmap(heatmap, judgement, policy):
    if judgement is not None:
        apply_levels(heatmap, judgement)
    return refresh_overall(heatmap, policy.overall_peak_share)


# Use case: a short summary of a finalized heatmap (levels applied), for the chat and the CI job summary.


def summarize_heatmap(presenter, heatmap, judgement, policy):
    """(the finalized Heatmap, its report from the ReportPresenter)."""
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    return heatmap, presenter.report(heatmap)


# Use case: put the heat map into the repository's README (the `## Risk Heat Map` section) and its diagram file,
# through the FileSystem interface and a DiagramPresenter.
#
# Nothing is written when the ratings are the same as in the README already (same fingerprint, diagram present): a
# scheduled refresh then has nothing to propose. The README keeps the commit and date of the last real change.


def update_readme(fs, presenter, readme_path, svg_path, heatmap, judgement, policy):
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    fp = fingerprint(heatmap)
    existing = fs.read_text(readme_path) if fs.is_file(readme_path) else None
    if block_fingerprint(existing) == fp and fs.is_file(svg_path):
        return ReadmeUpdate(heatmap=heatmap, changed=False, fingerprint=fp)
    link = fs.relative(svg_path, fs.parent(fs.absolute(readme_path))).replace(fs.sep, "/")
    text, action = merge_readme(existing, presenter.readme_block(heatmap, link, fp))
    fs.write_text(svg_path, presenter.svg(heatmap))
    fs.write_text(readme_path, text if text.endswith("\n") else text + "\n")
    return ReadmeUpdate(heatmap=heatmap, changed=True, action=action, fingerprint=fp)


def finalize_heatmap_report(fs, env, out):
    """The heat map's run in CI: its report is the deterministic summary (`out`/heatmap.md, redacted: the agent only
    levels), written to `out`/report.md. No result and no step outputs."""
    write_run_output(fs, out, fs.join(out, "heatmap.md"), None, secret_values(env.variables()))
    return None, StepOutputs({})
