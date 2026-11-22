"""Use cases: rate every sub-module of a repository on the four aspects. The working tree is read through the Git and
FileSystem interfaces, with the built-in graph or a KnowledgeGraph (code-review-graph); a revision is read straight
from git objects. The rating date comes from the Clock."""

from collections import Counter

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.rating import Facts, Heatmap, TopFile
from clean_eval.use_cases.coverage import coverage_line_rates
from clean_eval.use_cases.rules import classify
from clean_eval.use_cases.rules.ast_graph import builtin_graph
from clean_eval.use_cases.rules.graph import dependents, functions_by_module, test_files_to_modules
from clean_eval.use_cases.rules.modules import split_modules
from clean_eval.use_cases.rules.python_signals import file_signals
from clean_eval.use_cases.rules.rating import rate_module
from clean_eval.use_cases.sources import read_revision, read_working_tree


def rated_sources(files, policy):
    """The files the ratings cover: source (not tests) that the policy doesn't exclude."""
    return [f for f in files if not f.is_test and not classify.excluded(f.path, policy)]


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
            migrations=sum(1 for f in mod_files if classify.is_migration(f.path, policy)),
            entry_flows=[crit for crit, entry, _ in graph.flows if file_mod.get(entry) == name],
            flow_touch=sum(crit for crit, _, fs in graph.flows if any(file_mod.get(x) == name for x in fs))
            / total_crit,
            keyword=classify.has_core_keyword(name, policy),
            tooling=classify.looks_like_tooling(name, mod_files[0].path, policy),
            graph_functions=len(functions),
            linked_functions=len(linked),
            linked_test_files=len(test_files),
            linked_test_loc=sum(test_loc.get(t, 0) / max(len(test_mods[t]), 1) for t in test_files),
            test_scales=Counter(classify.classify_scale(t) for t in sorted(test_files)),
            line_rate=sum(r * w for r, w in rates) / max(sum(w for _, w in rates), 1) if rates else None,
            na_reason=classify.not_applicable(name, policy),
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
