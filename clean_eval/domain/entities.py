"""Entities: the plain data the business rules work on."""
from collections import Counter
from dataclasses import dataclass, field, fields
from typing import Optional


@dataclass
class SourceFile:
    path: str
    text: str
    loc: int
    is_test: bool
    blob: str = ""  # content id (git blob sha) when known, so per-file analysis can be reused across revisions


@dataclass
class PyStats:
    funcs: list = field(default_factory=list)  # (lines, complexity) per function
    typed: int = 0
    broad: int = 0
    swallow: int = 0


@dataclass
class FileSignals:
    """What one file says about risk, computed once per file content."""
    stats: Optional[PyStats]  # None when the file cannot be parsed
    todo: int
    effects: dict             # write-call kind -> (count, first line)


@dataclass
class Snapshot:
    """The code being rated: a working tree, or a revision read straight from git."""
    repo: str
    ref: str
    sha: str
    root: str


@dataclass
class Facts:
    """Everything the four aspect rules need to know about one module."""
    name: str
    loc: int
    files: int
    share: float                  # share of the repo's source lines
    funcs: list                   # (lines, complexity) per function
    typed_share: Optional[float]  # None when the module has no functions
    broad: int                    # broad exception handlers
    swallow: int                  # handlers that only pass or continue
    todo: int
    big_files: int
    fan_in: int                   # modules that import or call it
    fan_out: int                  # modules it imports or calls
    effect_counts: Counter
    effect_examples: dict
    migrations: int
    entry_flows: list             # criticality of each flow that starts here
    flow_touch: float             # share of total flow criticality passing through it
    keyword: bool
    tooling: bool
    graph_functions: int
    linked_functions: int         # functions with a resolved test link
    linked_test_files: int
    linked_test_loc: float
    test_scales: Counter
    line_rate: Optional[float]    # from a coverage report when the repo has one
    na_reason: Optional[str] = None  # set when the module is not rated for Unverified
    flows_available: bool = True     # False when the graph has no execution flows

    @property
    def kloc(self):
        return max(self.loc / 1000.0, 0.3)


def facts_to_dict(f):
    """JSON-ready copy of Facts (Counters become plain dicts, tuples lists)."""
    d = {x.name: getattr(f, x.name) for x in fields(f)}
    d["effect_counts"], d["test_scales"] = dict(f.effect_counts), dict(f.test_scales)
    d["funcs"] = [list(x) for x in f.funcs]
    return d


def facts_from_dict(d):
    known = {x.name for x in fields(Facts)}
    d = {k: v for k, v in d.items() if k in known}
    d["effect_counts"], d["test_scales"] = Counter(d.get("effect_counts") or {}), Counter(d.get("test_scales") or {})
    d["funcs"] = [tuple(x) for x in d.get("funcs") or []]
    return Facts(**d)


@dataclass
class ModuleRating:
    name: str
    loc: int
    files: int
    scores: dict       # biz, effects, entropy, unverified (None when N/A) and overall
    baseline: dict     # the four static aspect scores, before any level judgement
    tests: dict
    evidence: dict
    top_files: list
    facts: Optional[dict] = None  # the Facts behind the scores, so a calibration can re-score under another policy


@dataclass
class Heatmap:
    repo: str
    ref: str
    sha: str
    generated_at: str
    loc_total: int
    modules: list
    notes: list
    judged_modules: int = 0
    policy_version: Optional[int] = None


@dataclass
class ModuleRisk:
    """The risk a change carries in one module it touches, as rated at the change's base."""
    module: str
    band: str
    score: Optional[int]          # overall score; None when the module is not rated
    scores: dict
    evidence: dict
    rated: bool                   # False: no rated module covers the path (new module, or non-Python code)
    facts: Optional[dict] = None


@dataclass
class ChangeRisk:
    """The risk of a change: every touched module, and the most severe band among them."""
    files: dict                   # changed path -> module name (None when unrated or ignored)
    modules: list                 # ModuleRisk per touched module
    ignored: list                 # changed paths that carry no risk of their own (docs, images)
    max_band: str
    max_score: int
