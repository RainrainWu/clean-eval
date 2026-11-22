"""What a rating reads and produces: the code read from a repository (snapshot, files, per-file signals and
syntax), its knowledge graph, the rated modules and heat map, a reviewer's judgement of them, and the risk a change
carries."""

from collections import Counter
from typing import Annotated, Any, Literal, Optional, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from clean_eval.domain.policy import Band, Count, Fraction, Level, Score

# What is read from the code: a snapshot of a repository, its source files, and the signals one file gives.


class Snapshot(BaseModel):
    """The code being rated: a working tree, or a revision read straight from git."""

    model_config = ConfigDict(frozen=True)

    repo: str
    ref: str
    sha: str
    root: str


class SourceFile(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Annotated[str, Field(min_length=1)]  # repo-relative, "/"-separated
    text: str
    loc: Count
    is_test: bool
    blob: str = ""  # content id (git blob sha) when known, so per-file analysis can be reused across revisions


class PyStats(BaseModel):
    """What the syntax tree of one Python file shows."""

    funcs: list[tuple[Count, Count]] = []  # (lines, complexity) per function
    typed: Count = 0  # functions with every argument and the return annotated
    broad: Count = 0  # broad exception handlers
    swallow: Count = 0  # handlers that only pass or continue


class FileSignals(BaseModel):
    """What one file says about risk, computed once per file content."""

    stats: Optional[PyStats]  # None when the file cannot be parsed
    todo: Count
    effects: dict[str, tuple[Count, Count]]  # write-call kind -> (count, first line)


class FileSyntax(BaseModel):
    """What the syntax tree of one Python file says about its links: its functions, its imports and the names it uses."""

    model_config = ConfigDict(frozen=True)

    defs: tuple[str, ...] = ()  # functions and methods: "f", "Class.method"
    imports: tuple[tuple[str, int, tuple[str, ...]], ...] = ()  # (module, level, names)
    refs: frozenset[str] = frozenset()  # identifiers, attribute names and imported names used


# The knowledge graph as the business rules see it: relationships between files, functions and tests.


class Graph(BaseModel):
    nodes: dict[str, tuple[str, str, bool]]  # qualified name -> (kind, repo-relative file, is_test), Python only
    links: dict[str, set[str]]  # file -> files it imports or calls into (resolved edges only)
    tested: dict[str, set[str]]  # function qualified name -> test files linked to it
    stats: dict[str, Any]


# A rating: the facts the four aspect rules read about one module, the scores they give, and the heat map of a repository.
# Scores are 0-100 wherever they appear, and assignments are validated too, so no rule can push one out of range.


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
    keyword: bool
    tooling: bool
    graph_functions: Count
    linked_functions: Count  # functions with a resolved test link
    linked_test_files: Count
    linked_test_loc: float
    test_scales: Counter[str]
    line_rate: Optional[float]  # from a coverage report when the repo has one
    na_reason: Optional[str] = None  # set when the module is not rated for Unverified

    @property
    def kloc(
        self,
    ) -> float:
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


# A reviewer's judgement of a heat map: one level (0 most severe ... 3 least) per module and aspect, with a reason.

AspectName = Literal["business", "effects", "entropy", "unverified"]
MAX_REASON = 240


class AspectLevel(BaseModel):
    level: Level
    reason: str = ""

    @field_validator("reason", mode="before")
    @classmethod
    def shorten_reason(
        cls,
        reason: object,
    ) -> str:
        return ("" if reason is None else str(reason))[:MAX_REASON]


class Judgement(BaseModel):
    """{"modules": {name: {aspect: {"level": 0-3, "reason": ...}}}}. The levelling agent's output is untrusted: an entry
    that doesn't validate is dropped rather than failing the whole judgement, as the skill documents."""

    modules: dict[str, dict[AspectName, AspectLevel]] = {}

    @model_validator(mode="before")
    @classmethod
    def keep_valid_entries(
        cls,
        data: object,
    ) -> dict[str, dict[str, dict[str, AspectLevel]]]:
        modules = data.get("modules") if isinstance(data, dict) else None
        kept = {}
        for name, aspects in modules.items() if isinstance(modules, dict) else ():
            if not isinstance(name, str) or not isinstance(aspects, dict):
                continue
            levels = {}
            for aspect, entry in aspects.items():
                if aspect not in AspectName.__args__:
                    continue
                try:
                    levels[aspect] = AspectLevel.model_validate(entry)
                except ValidationError:
                    continue
            if levels:
                kept[name] = levels
        return {"modules": kept}


# The risk a change carries: every module it touches, as rated at the change's base.


class ModuleRecord(BaseModel):
    """A module a change touched, with its band at the change's base. A path no rated module covers (a new module, or
    non-Python code) is unrated: it has a band (the policy's unrated band) but no score."""

    module: str
    band: Band
    score: Optional[Score] = None
    scores: Optional[Scores] = None
    rated: bool
    facts: Optional[Facts] = None  # what a calibration re-scores under another policy

    @model_validator(mode="after")
    def check_rated_means_scored(
        self,
    ) -> Self:
        if self.rated != (self.score is not None):
            raise ValueError("a rated module has a score and an unrated one has none")
        return self


class ModuleRisk(ModuleRecord):
    evidence: Optional[Evidence] = None


class ChangeRisk(BaseModel):
    """The risk of a change: every touched module, and the most severe band among them."""

    files: dict[str, Optional[str]]  # changed path -> module name (None when unrated or ignored)
    modules: list[ModuleRisk]
    ignored: list[str]  # changed paths that carry no risk of their own (docs, images)
    max_band: Band
    max_score: Score
