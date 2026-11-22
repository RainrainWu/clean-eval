"""Merged-PR history: the period looked back over, each merged PR rated at its base, the later fixes and reverts that point
back at it ("escaped defects"), what its review cited, and how well the scores predicted the defects (calibration)."""
import datetime as dt
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from clean_eval.domain.change import ModuleRecord
from clean_eval.domain.values import Band, Count, Fraction, Score, Sha


class Period(BaseModel):
    """The PRs merged from `since` to `until` (inclusive days), and how long after each one a fix still counts."""
    since: dt.date
    until: dt.date
    window_days: Count = 30

    @model_validator(mode="after")
    def _in_order(self):
        if self.since > self.until:
            raise ValueError("since (%s) is after until (%s)" % (self.since, self.until))
        return self

    @property
    def until_ts(self):
        """The end of `until` (23:59:59 UTC), unix time."""
        return dt.datetime.combine(self.until, dt.time(23, 59, 59), tzinfo=dt.timezone.utc).timestamp()

    @property
    def lookahead_until(self):
        """The last day a fix of a PR in the period is looked for."""
        return self.until + dt.timedelta(days=self.window_days)


class DefectLink(BaseModel):
    """A later fix or revert attributed to a PR: `high` when the fix names it, else `heuristic` (shared files)."""
    fix_sha: Sha
    fix_pr: Optional[int] = None
    subject: str
    kind: Literal["fix", "revert"]
    confidence: Literal["high", "heuristic"]
    days_after: float
    overlap_files: list[str]


class ReviewSignal(BaseModel):
    """What a PR's reviews said: the rules cited, NEW findings, and the clean-eval marker of a trusted reviewer."""
    model_config = ConfigDict(extra="forbid")  # so a fetch failure can never pass for a review

    url: Optional[str] = None
    labels: list[Optional[str]] = []
    size: Count = 0
    review_comment_count: Count = 0
    rules_cited: list[str] = []
    new_findings: Count = 0
    clean_eval_marker: Optional[dict[str, str]] = None
    untrusted_markers_ignored: Count = 0


class FetchFailure(BaseModel):
    """A PR whose reviews couldn't be fetched: `not_found` (no such PR) or `error`."""
    model_config = ConfigDict(extra="forbid")

    error: Literal["not_found", "error"]
    message: str


class MergedPR(BaseModel):
    """One merged PR: the first-parent commit that merged it, the base it was merged onto, what it changed and touched."""
    sha: Sha
    base: Sha
    author: str
    ts: int
    subject: str
    pr: Optional[int] = None
    files: list[str] = Field(default_factory=list, exclude=True)  # stored as a count only
    file_count: Count = 0
    max_tier: Band = "Low"
    max_score: Score = 0
    modules: list[ModuleRecord] = []
    defects: list[DefectLink] = []
    review: Optional[Union[ReviewSignal, FetchFailure]] = None

    @model_validator(mode="before")
    @classmethod
    def _count_files(cls, data):
        if isinstance(data, dict) and "file_count" not in data:
            data = {**data, "file_count": len(data.get("files") or [])}
        return data


class BandStats(BaseModel):
    prs: Count = 0
    defective_prs: Count = 0
    escape_rate: Optional[Fraction] = None


class AspectMeans(BaseModel):
    defective: Optional[float] = None
    clean: Optional[float] = None


class Replay(BaseModel):
    """How a policy's scores separate defective from clean PRs."""
    auc_max_score: Optional[Fraction] = None  # P(a defective PR outscores a clean one); None when undefined
    by_band: dict[Band, BandStats] = {}
    band_mix: dict[Band, Fraction] = {}


class Calibration(Replay):
    auc_note: str = "0.5 = the overall score is no better than random at predicting escaped defects; 1.0 = perfect"
    aspect_auc: dict[str, Optional[Fraction]] = {}
    aspect_means: dict[str, AspectMeans] = {}


class SensitivityRow(Replay):
    change: str  # the one knob changed, e.g. "business_weights.dependents 0.3→0.4 (+0.1)", or "baseline"


class ModuleStats(BaseModel):
    prs: Count
    defective_prs: Count
    escape_rate: Fraction
    mean_score: Optional[float] = None
    bands_seen: dict[Band, Count] = {}


class RuleUse(BaseModel):
    prs_cited: Count = 0
    cited_in_defective_prs: Count = 0


class RuleStats(BaseModel):
    """How the review rules did on the PRs whose reviews were fetched; unreliable when too many fetches failed."""
    per_rule: dict[str, RuleUse] = {}
    prs_reviewed: Count = 0
    prs_fetch_failed: list[int] = []
    prs_not_found: list[int] = []
    gh_error_rate: Fraction = 0.0
    reliable: bool = True
    prs_with_clean_eval_review: Count = 0
    new_findings: Count = 0
    defective_prs_with_no_rule_cited: list[int] = []


class Totals(BaseModel):
    prs: Count
    defective_prs: Count
    escape_rate: Optional[Fraction] = None


class HistoryPeriod(BaseModel):
    since: dt.date
    until: dt.date
    branch: str
    model_source: Optional[str] = None
    defect_window_days: Count
    policy_version: int


class EvalHistory(BaseModel):
    """eval-history.json: what a replay re-scores and what the eval agent reads."""
    period: HistoryPeriod
    totals: Totals
    calibration: Calibration
    modules: dict[str, ModuleStats] = {}
    rules: Optional[RuleStats] = None
    prs: list[MergedPR] = []


class DefectPatch(BaseModel):
    """One defect link whose PR and fix patches were written for the eval agent to verify (defects/index.json)."""
    file_stem: str
    pr: Optional[int]
    pr_sha: Sha
    pr_band: Band
    fix_sha: Sha
    fix_subject: str
    kind: Literal["fix", "revert"]
    confidence: Literal["high", "heuristic"]
    overlap_files: list[str]


class EvalContext(BaseModel):
    """context.json of an eval."""
    since: dt.date
    until: dt.date
    prs: Count
    defective_prs: Count
    auc: Optional[Fraction]
    model_source: Optional[str]
    model_existed: bool
    rules_existed: bool
    with_gh: bool
    rules_reliable: Optional[bool]
    defect_patches: Count
    defect_links: Count
