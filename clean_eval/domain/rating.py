"""A rating: the facts the four aspect rules read about one module, the scores they give, and the heat map of a repository.
Scores are 0-100 wherever they appear, and assignments are validated too, so no rule can push one out of range."""

from collections import Counter
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from clean_eval.domain.source import FileSignals, FileSyntax
from clean_eval.domain.values import Count, Fraction, Score


class Facts(BaseModel):
    """Everything the four aspect rules need to know about one module."""

    name: str
    loc: Count
    files: Count
    share: Fraction  # share of the repo's source lines
    funcs: list[tuple[Count, Count]]  # (lines, complexity) per function
    typed_share: Optional[Fraction]  # None when the module has no functions
    broad: Count  # broad exception handlers
    swallow: Count  # handlers that only pass or continue
    todo: Count
    big_files: Count
    fan_in: Count  # modules that import or call it
    fan_out: Count  # modules it imports or calls
    effect_counts: Counter[str]
    effect_examples: dict[str, str]
    migrations: Count
    entry_flows: list[float]  # criticality of each flow that starts here
    flow_touch: float  # share of total flow criticality passing through it
    keyword: bool
    tooling: bool
    graph_functions: Count
    linked_functions: Count  # functions with a resolved test link
    linked_test_files: Count
    linked_test_loc: float
    test_scales: Counter[str]
    line_rate: Optional[float]  # from a coverage report when the repo has one
    na_reason: Optional[str] = None  # set when the module is not rated for Unverified
    flows_available: bool = True  # False when the graph has no execution flows

    @property
    def kloc(self):
        return max(self.loc / 1000.0, 0.3)


class AspectScores(BaseModel):
    """The four aspect scores of a module; unverified is None when the aspect doesn't apply (N/A)."""

    model_config = ConfigDict(validate_assignment=True)

    biz: Score
    effects: Score
    entropy: Score
    unverified: Optional[Score]


class Scores(AspectScores):
    """The aspect scores and the overall score that combines them."""

    overall: Score


class Evidence(BaseModel):
    """Why each aspect scored what it did, one line per reason."""

    biz: list[str] = []
    effects: list[str] = []
    entropy: list[str] = []
    unverified: list[str] = []


class ModuleTests(BaseModel):
    """The tests linked to one module."""

    functions: Count  # functions in the graph
    tested: Count  # ... with a resolved test link
    coverage: Optional[Score]  # tested / functions, in percent; None without functions
    files: Count  # linked test files
    loc: Count  # their lines, shared across the modules they cover
    ratio: float  # those lines per source line
    scales: dict[str, Count] = {}  # linked test files by scale (unit, integration, e2e, other)


class TopFile(BaseModel):
    path: str
    loc: Count
    max_fn: Optional[Count]  # lines of its longest function; None when it can't be parsed


class ModuleRating(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    name: str
    loc: Count
    files: Count
    scores: Scores
    baseline: AspectScores  # the four static aspect scores, before any level judgement
    tests: ModuleTests
    evidence: Evidence
    top_files: list[TopFile] = []
    facts: Optional[Facts] = None  # the facts behind the scores, so a calibration can re-score under another policy


class Heatmap(BaseModel):
    model_config = ConfigDict(validate_assignment=True)

    repo: str
    ref: str
    sha: str
    generated_at: str
    loc_total: Count
    modules: list[ModuleRating] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    judged_modules: Count = 0
    policy_version: Optional[int] = None


class ReadmeUpdate(BaseModel):
    """What refreshing the README's heat-map section did."""

    heatmap: Heatmap
    changed: bool
    action: Optional[Literal["ADD", "UPDATE"]] = None  # when changed
    fingerprint: str


class RatingCache(BaseModel):
    """What rating many revisions of one repository shares, so each version of a file is read and analysed once: blob
    texts by id, and the syntax and signals of each file content."""

    blobs: dict[str, str] = {}
    syntax: dict[str, FileSyntax] = {}
    signals: dict[str, FileSignals] = {}
