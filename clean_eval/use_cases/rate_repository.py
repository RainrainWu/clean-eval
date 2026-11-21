"""Use case: rate every sub-module of a snapshot of a repository on the four aspects."""
from collections import Counter
from dataclasses import dataclass

from ..domain.errors import NoPythonSource
from ..domain.rating import Facts, Heatmap, TopFile
from .rules import classify
from .rules.graph import dependents, functions_by_module, test_files_to_modules
from .rules.modules import split_modules
from .rules.rating import rate_module


@dataclass
class _Context:  # what rating every module of one snapshot shares (working state, not a domain model)
    graph: object
    file_mod: dict
    deps: tuple
    funcs_of: dict
    test_mods: dict
    test_loc: dict
    total_crit: float
    total_loc: int
    signals: dict
    coverage: dict


class RateRepository:
    def __init__(self, source, graph, signals, coverage):
        self.source, self.graph, self.signals, self.coverage = source, graph, signals, coverage

    def execute(self, policy, today, depth=None, max_modules=None):
        snap = self.source.snapshot()
        files = self.source.read()
        src = [f for f in files if not f.is_test and not classify.excluded(f.path, policy)]
        mods, notes = split_modules(src, policy, depth, max_modules)
        if not mods:
            raise NoPythonSource("no Python source files in %s at %s" % (snap.repo, snap.ref))
        g = self.graph.graph_for(snap, files)
        notes.insert(0, "Knowledge graph (%s): %d nodes, %d edges, %d flows"
                     % (g.stats.get("source", "graph"), g.stats.get("nodes", 0), g.stats.get("edges", 0), g.stats.get("flows", 0)))
        file_mod = {f.path: m for m, fl in mods.items() for f in fl}
        ctx = _Context(
            graph=g, file_mod=file_mod, deps=dependents(g, file_mod), funcs_of=functions_by_module(g, file_mod),
            test_mods=test_files_to_modules(g, file_mod), test_loc={f.path: f.loc for f in files if f.is_test},
            total_crit=sum(c for c, _, _ in g.flows) or 1, total_loc=sum(f.loc for f in src) or 1,
            signals={f.path: self.signals.file_signals(f) for fl in mods.values() for f in fl},
            coverage=self.coverage.line_rates(snap))
        ratings = [rate_module(self._facts(name, fl, ctx, policy), self._top_files(fl, ctx), policy) for name, fl in mods.items()]
        ratings.sort(key=lambda r: -r.loc)
        notes.append("Unverified uses test links from the graph" + ("" if ctx.coverage else "; no coverage report in the repo"))
        return Heatmap(repo=snap.repo, ref=snap.ref, sha=snap.sha, generated_at=today, loc_total=ctx.total_loc,
                       modules=ratings, notes=notes, policy_version=policy.version)

    @staticmethod
    def _facts(name, files, c, p):
        fan_in, fan_out = c.deps
        loc = sum(f.loc for f in files)
        sig = [c.signals[f.path] for f in files]
        py = [s.stats for s in sig if s.stats]
        n = sum(len(s.funcs) for s in py)
        counts, examples = Counter(), {}
        for f in sorted(files, key=lambda x: x.path):
            for kind, (count, line) in c.signals[f.path].effects.items():
                counts[kind] += count
                examples.setdefault(kind, "%s:%d" % (f.path, line))
        functions = c.funcs_of.get(name, [])
        linked = [q for q in functions if c.graph.tested.get(q)]
        test_files = set().union(*[c.graph.tested[q] for q in linked]) if linked else set()
        rates = [(c.coverage[f.path], f.loc) for f in files if f.path in c.coverage]
        return Facts(
            name=name, loc=loc, files=len(files), share=loc / c.total_loc,
            funcs=[x for s in py for x in s.funcs],
            typed_share=sum(s.typed for s in py) / n if n else None,
            broad=sum(s.broad for s in py), swallow=sum(s.swallow for s in py),
            todo=sum(s.todo for s in sig),
            big_files=sum(1 for f in files if f.loc > p.big_file_lines),
            fan_in=len(fan_in[name]), fan_out=len(fan_out[name]),
            effect_counts=counts, effect_examples=examples,
            migrations=sum(1 for f in files if classify.is_migration(f.path, p)),
            entry_flows=[crit for crit, entry, _ in c.graph.flows if c.file_mod.get(entry) == name],
            flow_touch=sum(crit for crit, _, fs in c.graph.flows if any(c.file_mod.get(x) == name for x in fs)) / c.total_crit,
            keyword=classify.has_core_keyword(name, p), tooling=classify.looks_like_tooling(name, files[0].path, p),
            graph_functions=len(functions), linked_functions=len(linked), linked_test_files=len(test_files),
            linked_test_loc=sum(c.test_loc.get(t, 0) / max(len(c.test_mods[t]), 1) for t in test_files),
            test_scales=Counter(classify.classify_scale(t) for t in sorted(test_files)),
            line_rate=sum(r * w for r, w in rates) / max(sum(w for _, w in rates), 1) if rates else None,
            na_reason=classify.not_applicable(name, p), flows_available=c.graph.has_flows)

    @staticmethod
    def _top_files(files, c):
        top = sorted(files, key=lambda f: -f.loc)[:5]
        out = []
        for f in top:
            stats = c.signals[f.path].stats
            out.append(TopFile(path=f.path, loc=f.loc, max_fn=max((x[0] for x in stats.funcs), default=0) if stats else None))
        return out
