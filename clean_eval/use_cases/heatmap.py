"""Use cases of the risk heat map: rate a working tree or a revision (read through Git and the FileSystem, with the
built-in graph), apply a reviewer's levels, summarize it, and put it into the README."""

import hashlib
import json
import re
from collections import (
    Counter,
    defaultdict,
)
from typing import Optional

from clean_eval.domain.errors import (
    HeatmapError,
    NoPythonSource,
)
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import (
    Facts,
    FileSignals,
    Graph,
    Heatmap,
    Judgement,
    ModuleRating,
    RatingCache,
    ReadmeUpdate,
    Snapshot,
    SourceFile,
    TopFile,
)
from clean_eval.domain.review import (
    RunFolders,
    StepOutputs,
)
from clean_eval.settings import Settings
from clean_eval.use_cases.ci import (
    report_run,
    run_agent,
)
from clean_eval.use_cases.interfaces import (
    Agent,
    Clock,
    DiagramPresenter,
    Environment,
    FileSystem,
    Git,
    ReportPresenter,
    RunLog,
)
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
from clean_eval.use_cases.rules.review import (
    redact,
    secret_values,
)
from clean_eval.use_cases.store import (
    load_policy,
    save_heatmap,
    write_run_output,
    write_step_outputs,
)

# The sources to rate, through the Git and FileSystem interfaces: the Python files of a working tree, or of any revision
# read straight from git objects. Files are only read as text; nothing from the repository is imported or run.

GENERATED = re.compile(r"(_pb2(_grpc)?\.py$|/generated/|/vendor/|/site-packages/)")
MAX_FILE_BYTES = 1_500_000


def count_loc(
    text: str,
) -> int:
    return sum(1 for line in text.splitlines() if line.strip() and not line.strip().startswith("#"))


def is_python_source(
    path: str,
) -> bool:
    return path.endswith(".py") and not GENERATED.search("/" + path)


def content_id(
    text: str,
) -> str:
    """Same id as `git hash-object` for UTF-8 text, so working-tree and revision reads share caches."""
    data = text.encode("utf-8", "surrogateescape")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def working_tree_snapshot(
    git: Git,
) -> Snapshot:
    sha = git.rev_parse("HEAD", short=True)
    return Snapshot(
        repo=git.repo_name(),
        ref=git.branch() or sha,
        sha=sha,
        root=git.toplevel(),
    )


def revision_snapshot(
    git: Git,
    rev: str,
) -> Snapshot:
    return Snapshot(
        repo=git.repo_name(),
        ref=rev,
        sha=git.rev_parse(rev, short=True),
        root=git.toplevel(),
    )


def read_working_tree(
    git: Git,
    fs: FileSystem,
    policy: Policy,
) -> tuple[Snapshot, list[SourceFile]]:
    """(Snapshot, the tracked Python files of the working tree as they are on disk now)."""
    snapshot, out = working_tree_snapshot(git), []
    for rel in git.ls_files():
        if not is_python_source(rel):
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
            SourceFile(
                path=rel,
                text=text,
                loc=count_loc(text),
                is_test=is_test(rel, policy),
                blob=content_id(text),
            )
        )
    return snapshot, out


def read_revision(
    git: Git,
    rev: str,
    policy: Policy,
    blobs: Optional[dict[str, str]] = None,
) -> tuple[Snapshot, list[SourceFile]]:
    """(Snapshot, the Python files of a revision), read from git objects (no checkout). `blobs` ({blob id: text}, e.g. a
    RatingCache's) is shared across revisions, so each file version is read once."""
    blobs = {} if blobs is None else blobs
    entries = [(p, b) for p, b, size in git.ls_tree(rev) if is_python_source(p) and size <= MAX_FILE_BYTES]
    blobs.update(git.read_blobs([b for _, b in entries if b not in blobs]))
    files = [
        SourceFile(
            path=p,
            text=blobs[b],
            loc=count_loc(blobs[b]),
            is_test=is_test(p, policy),
            blob=b,
        )
        for p, b in entries
        if b in blobs
    ]
    return revision_snapshot(git, rev), files


# Line coverage from a Cobertura coverage.xml in a working tree, when there is one (revisions have none), through the
# FileSystem interface.

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')


def coverage_line_rates(
    fs: FileSystem,
    root: str,
) -> dict[str, float]:
    """{file path: line rate 0-1} from the first coverage.xml at or below `root` (one level), or {}."""
    for report in fs.glob(root, "coverage.xml") + fs.glob(root, "*/coverage.xml"):
        if fs.is_link(report):
            continue
        return {m.group(1): float(m.group(2)) for m in CLASS.finditer(fs.read_text(report))}
    return {}


# Use cases: rate every sub-module of a repository on the four aspects. The working tree is read through the Git and
# FileSystem interfaces, with the built-in graph; a revision is read straight from git objects. The rating date comes from the Clock.


def rated_sources(
    files: list[SourceFile],
    policy: Policy,
) -> list[SourceFile]:
    """The files the ratings cover: source (not tests) that the policy doesn't exclude."""
    return [f for f in files if not f.is_test and not analysis.excluded(f.path, policy)]


def no_python_error(
    snapshot: Snapshot,
) -> NoPythonSource:
    return NoPythonSource(
        "no Python source files in %s at %s" % (snapshot.repo, snapshot.ref),
    )


def module_effects(
    mod_files: list[SourceFile],
    signals: dict[str, FileSignals],
) -> tuple[Counter[str], dict[str, str]]:
    """The module's side effects, from its files' `signals`: how many of each kind, and where each kind first appears
    (`path:line`, its files taken by path)."""
    counts, examples = Counter(), {}
    for f in sorted(mod_files, key=lambda source_file: source_file.path):
        for kind, (count, line) in signals[f.path].effects.items():
            counts[kind] += count
            examples.setdefault(kind, "%s:%d" % (f.path, line))
    return counts, examples


def linked_tests(
    functions: list[str],
    graph: Graph,
) -> tuple[list[str], set[str]]:
    """Which of a module's `functions` the knowledge `graph` links to tests, and the test files that link them."""
    linked = [q for q in functions if graph.tested.get(q)]
    return linked, set().union(*[graph.tested[q] for q in linked]) if linked else set()


def weighted_line_rate(
    mod_files: list[SourceFile],
    line_rates: dict[str, float],
) -> Optional[float]:
    """A module's line rate from the coverage report's `line_rates`, its files weighted by their size; None when the
    report covers none of them."""
    rates = [(line_rates[f.path], f.loc) for f in mod_files if f.path in line_rates]
    return sum(r * w for r, w in rates) / max(sum(w for _, w in rates), 1) if rates else None


def module_facts(
    name: str,
    mod_files: list[SourceFile],
    signals: dict[str, FileSignals],
    graph: Graph,
    funcs_of: defaultdict[str, list[str]],
    fan_in: defaultdict[str, set[str]],
    fan_out: defaultdict[str, set[str]],
    test_loc: dict[str, int],
    test_mods: defaultdict[str, set[str]],
    line_rates: dict[str, float],
    total_loc: int,
    policy: Policy,
) -> Facts:
    """The Facts of the module `name`, made of `mod_files`, from what rate_modules reads of the whole repository."""
    loc = sum(f.loc for f in mod_files)
    py = [s for s in (signals[f.path].stats for f in mod_files) if s is not None]
    n = sum(len(s.funcs) for s in py)
    counts, examples = module_effects(mod_files, signals)
    functions = funcs_of.get(name, [])
    linked, test_files = linked_tests(functions, graph)
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
        keyword=analysis.has_core_keyword(name, policy),
        tooling=analysis.looks_like_tooling(name, mod_files[0].path, policy),
        graph_functions=len(functions),
        linked_functions=len(linked),
        linked_test_files=len(test_files),
        linked_test_loc=sum(test_loc.get(t, 0) / max(len(test_mods[t]), 1) for t in test_files),
        test_scales=Counter(
            (analysis.classify_scale(t) for t in sorted(test_files)),
        ),
        line_rate=weighted_line_rate(mod_files, line_rates),
        na_reason=analysis.not_applicable(name, policy),
    )


def module_top_files(
    mod_files: list[SourceFile],
    signals: dict[str, FileSignals],
) -> list[TopFile]:
    """The module's five largest files, each with the length of its longest function (from its `signals`)."""
    out = []
    for f in sorted(mod_files, key=lambda source_file: -source_file.loc)[:5]:
        stats = signals[f.path].stats
        out.append(
            TopFile(
                path=f.path,
                loc=f.loc,
                max_fn=max((x[0] for x in stats.funcs), default=0) if stats else None,
            )
        )
    return out


def rate_modules(
    mods: dict[str, list[SourceFile]],
    files: list[SourceFile],
    graph: Graph,
    policy: Policy,
    line_rates: dict[str, float],
    total_loc: int,
    signals_cache: Optional[dict[str, FileSignals]],
) -> list[ModuleRating]:
    """Each module's rating, largest first, from what the whole repository holds: each file's signals (shared through
    `signals_cache`); the knowledge `graph`, with each module's functions and the modules each one is depended on by
    and depends on; each test file's size and modules; the coverage report's `line_rates`; and the lines of rated code
    (`total_loc`)."""
    file_mod = {f.path: m for m, fl in mods.items() for f in fl}
    fan_in, fan_out = dependents(graph, file_mod)
    funcs_of, test_mods = functions_by_module(graph, file_mod), test_files_to_modules(graph, file_mod)
    test_loc = {f.path: f.loc for f in files if f.is_test}
    signals = {f.path: file_signals(f, signals_cache) for fl in mods.values() for f in fl}
    ratings = [
        rate_module(
            module_facts(
                name, fl, signals, graph, funcs_of, fan_in, fan_out, test_loc, test_mods, line_rates, total_loc, policy
            ),
            module_top_files(fl, signals),
            policy,
        )
        for name, fl in mods.items()
    ]
    return sorted(ratings, key=lambda rating: -rating.loc)


def rate(
    snapshot: Snapshot,
    files: list[SourceFile],
    graph: Graph,
    policy: Policy,
    today: str,
    coverage: Optional[dict[str, float]] = None,
    signals_cache: Optional[dict[str, FileSignals]] = None,
    depth: Optional[int] = None,
    max_modules: Optional[int] = None,
) -> Heatmap:
    """The Heatmap of a snapshot's files, given their knowledge graph and coverage ({path: line rate})."""
    src = rated_sources(files, policy)
    mods, notes = split_modules(src, policy, depth, max_modules)
    if not mods:
        raise no_python_error(snapshot)
    notes.insert(
        0,
        "Knowledge graph (%s): %d nodes, %d edges"
        % (graph.stats.get("source", "graph"), graph.stats.get("nodes", 0), graph.stats.get("edges", 0)),
    )
    total_loc = sum(f.loc for f in src) or 1
    ratings = rate_modules(mods, files, graph, policy, coverage or {}, total_loc, signals_cache)
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


def rate_working_tree(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    policy: Policy,
    depth: Optional[int] = None,
    max_modules: Optional[int] = None,
) -> Heatmap:
    """The Heatmap of the working tree as it is on disk."""
    snapshot, files = read_working_tree(git, fs, policy)
    if not rated_sources(files, policy):
        raise no_python_error(snapshot)
    return rate(
        snapshot,
        files,
        builtin_graph(files),
        policy,
        clock.today(),
        coverage_line_rates(fs, snapshot.root),
        depth=depth,
        max_modules=max_modules,
    )


def rate_revision(
    git: Git,
    clock: Clock,
    rev: str,
    policy: Policy,
    cache: RatingCache,
    depth: Optional[int] = None,
    max_modules: Optional[int] = None,
) -> Heatmap:
    """The Heatmap of a revision, read from git objects. `cache` (a RatingCache) shares file contents and analysis
    across revisions, so rating many of them reads and parses each file version once."""
    snapshot, files = read_revision(git, rev, policy, cache.blobs)
    if not rated_sources(files, policy):
        raise no_python_error(snapshot)
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


def finalize_heatmap(
    heatmap: Heatmap,
    judgement: Optional[Judgement],
    policy: Policy,
) -> Heatmap:
    if judgement is not None:
        apply_levels(heatmap, judgement)
    return refresh_overall(heatmap, policy.overall_peak_share)


# Use case: a short summary of a finalized heatmap (levels applied), for the chat and the CI job summary.


def summarize_heatmap(
    presenter: ReportPresenter,
    heatmap: Heatmap,
    judgement: Optional[Judgement],
    policy: Policy,
) -> tuple[Heatmap, str]:
    """(the finalized Heatmap, its report from the ReportPresenter)."""
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    return heatmap, presenter.report(heatmap)


# Use case: put the heat map into the repository's README (the `## Risk Heat Map` section) and its diagram file,
# through the FileSystem interface and a DiagramPresenter.
#
# Nothing is written when the ratings are the same as in the README already (same fingerprint, diagram present): a
# scheduled refresh then has nothing to propose. The README keeps the commit and date of the last real change.


def update_readme(
    fs: FileSystem,
    presenter: DiagramPresenter,
    readme_path: str,
    svg_path: str,
    heatmap: Heatmap,
    judgement: Optional[Judgement],
    policy: Policy,
) -> ReadmeUpdate:
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    fp = fingerprint(heatmap)
    existing = fs.read_text(readme_path) if fs.is_file(readme_path) else None
    if block_fingerprint(existing) == fp and fs.is_file(svg_path):
        return ReadmeUpdate(
            heatmap=heatmap,
            changed=False,
            fingerprint=fp,
        )
    link = fs.relative(svg_path, fs.parent(fs.absolute(readme_path))).replace(fs.sep, "/")
    text, action = merge_readme(existing, presenter.readme_block(heatmap, link, fp))
    fs.write_text(svg_path, presenter.svg(heatmap))
    fs.write_text(readme_path, text if text.endswith("\n") else text + "\n")
    return ReadmeUpdate(
        heatmap=heatmap,
        changed=True,
        action=action,
        fingerprint=fp,
    )


def finalize_heatmap_report(
    fs: FileSystem,
    env: Environment,
    out: str,
) -> tuple[None, StepOutputs]:
    """The heat map's run in CI: its report is the deterministic summary (`out`/heatmap.md, redacted: the agent only
    levels), written to `out`/report.md. No result and no step outputs."""
    write_run_output(fs, out, fs.join(out, "heatmap.md"), None, secret_values(env.variables()))
    return None, StepOutputs(
        {},
    )


# Use case: the risk heat map's CI run (the action's risk-heat-map skill): rate every module, let the agent level the
# four aspects when asked to, publish the heat map into the README and its diagram, and report. Through the Git,
# FileSystem, Clock, Environment, Agent and RunLog interfaces and the two presenters.

LEVEL_PROMPT = (
    "Level the four risk aspects of every module in the heat map. Read %(context)s/skills/risk-heat-map/SKILL.md and "
    "follow its 'CI mode' section; the level standards are in %(context)s/skills/risk-heat-map/references/rate.md. "
    "Input: %(context)s/heatmap/data.json; the repository is checked out in the current directory. Write "
    "%(agent)s/judgement.json. %(extra)s"
)


def agent_judgement(
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
) -> Optional[Judgement]:
    """The levels the agent wrote (judgement.json in its folder), redacted of the environment's secrets first. None,
    with a warning, when it wrote none or wrote something other than a JSON object: the heat map then keeps its static
    levels. The Judgement drops any entry that doesn't validate."""
    path = fs.join(folders.agent, "judgement.json")
    if not fs.is_file(path):
        log.warning("the agent wrote no judgement.json; publishing the static levels")
        return None
    text, _ = redact(fs.read_text(path), secret_values(env.variables()))
    fs.write_text(path, text)
    try:
        levels = json.loads(text)
    except ValueError:
        levels = None
    if not isinstance(levels, dict):
        log.warning("the agent's judgement.json is not a JSON object; publishing the static levels")
        return None
    return Judgement.model_validate(levels)


def level_with_the_agent(
    agent: Agent,
    fs: FileSystem,
    env: Environment,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
) -> Optional[Judgement]:
    """The levels the agent gives the four aspects of every module, when the settings say `use_claude`; else None."""
    if not settings.heatmap.use_claude:
        return None
    agent.require()
    prompt = LEVEL_PROMPT % {
        "context": folders.context,
        "agent": folders.agent,
        "extra": settings.agent.extra_prompt,
    }
    run_agent(agent, fs, env, log, folders, prompt.strip())
    return agent_judgement(fs, env, log, folders)


def publish_heatmap(
    fs: FileSystem,
    env: Environment,
    diagram: DiagramPresenter,
    report: ReportPresenter,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
    heatmap: Heatmap,
    judgement: Optional[Judgement],
    policy: Policy,
) -> None:
    """Put the heat map into the settings' README and its diagram (both relative to the workspace), and report: the
    summary as the run's report, the finalized heat map as its result, `readme-changed` as a step output, and the report
    in the job summary. Each takes its own copy of the heat map: finalizing one applies the levels to it in place."""
    readme = settings.heatmap.readme
    update = update_readme(
        fs,
        diagram,
        fs.join(folders.workspace, readme),
        fs.join(folders.workspace, settings.heatmap.diagram),
        heatmap.model_copy(deep=True),
        judgement,
        policy,
    )
    final, text = summarize_heatmap(report, heatmap.model_copy(deep=True), judgement, policy)
    log.show("%s: %s (fingerprint %s)" % (readme, "updated" if update.changed else "unchanged", update.fingerprint))
    fs.write_text(fs.join(folders.out, "heatmap.md"), text)
    save_heatmap(fs, final, fs.join(folders.out, "result.json"))
    finalize_heatmap_report(fs, env, folders.out)
    write_step_outputs(
        fs,
        settings.github.step_outputs,
        StepOutputs(
            {"readme-changed": "true" if update.changed else "false"},
        ),
    )
    report_run(fs, settings.github.step_summary, "Risk Heat Map", folders)


def run_heatmap_action(
    git: Git,
    fs: FileSystem,
    clock: Clock,
    env: Environment,
    agent: Agent,
    diagram: DiagramPresenter,
    report: ReportPresenter,
    log: RunLog,
    folders: RunFolders,
    settings: Settings,
) -> None:
    """The risk heat map's CI run on `folders.workspace`: rate every module; when the settings say `use_claude`, let
    the agent level the four aspects (level_with_the_agent); then publish and report the heat map (publish_heatmap)."""
    log.step("Rate every module")
    policy, source = load_policy(fs, folders.workspace)
    heatmap = rate_working_tree(git, fs, clock, policy)
    heatmap.notes.append("Policy: %s" % source)
    save_heatmap(fs, heatmap, fs.join(folders.context, "heatmap", "data.json"))
    judgement = level_with_the_agent(agent, fs, env, log, folders, settings)
    log.step("Publish the heat map")
    publish_heatmap(fs, env, diagram, report, log, folders, settings, heatmap, judgement, policy)
