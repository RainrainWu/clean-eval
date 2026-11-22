"""A PR review: the rules it applies, the findings the review agent proposes (untrusted, checked field by field), the
review CI trusts and posts, and the PR and change it is about."""
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel, computed_field, field_validator, model_validator

from clean_eval.domain.change import ModuleRisk
from clean_eval.domain.values import Band, Count, OneLine, Score, Severity, Sha

RuleId = Annotated[str, Field(pattern=r"^[A-Z]{1,4}-\d{2,3}$")]
FindingRule = Annotated[str, Field(pattern=r"^(?:[A-Z]{1,4}-\d{2,3}|NEW)$")]  # NEW: a problem no rule covers
LineNumber = Annotated[int, Field(ge=1, strict=True)]
LEGACY_BANDS = {"Medium": "Moderate"}  # "Medium" is the pre-v2 name of the Moderate band
MAX_FINDINGS = 50


class ReviewRule(BaseModel):
    """One row of a rules table: `| ID | severity | min tier | rule |`."""
    id: RuleId
    severity: Severity
    min_tier: Band

    @field_validator("min_tier", mode="before")
    @classmethod
    def _legacy_band(cls, tier):
        return LEGACY_BANDS.get(tier, tier)  # rules files copied from the pre-v2 default keep working


class RulesTable(BaseModel):
    """The rules of a rules file, as a review reads them (a later row with the same ID wins)."""
    rules: list[ReviewRule] = []

    @property
    def by_id(self):
        return {r.id: r for r in self.rules}


class CheckedRules(RulesTable):
    """A rules file an unattended eval may keep: at least one rule row, and no ID twice."""

    @model_validator(mode="after")
    def _usable(self):
        if not self.rules:
            raise ValueError("review-rules.md has no rule rows")
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("review-rules.md has duplicate rule IDs: %s" % ", ".join(sorted({i for i in ids if ids.count(i) > 1})))
        return self


class AgentFinding(BaseModel):
    """One finding as the review agent proposed it. Text is cut to size; anything malformed refuses the whole result."""
    rule: FindingRule
    severity: Severity
    file: Optional[OneLine] = None
    line: Optional[LineNumber] = None
    summary: str = ""
    fix: str = ""

    @field_validator("file", mode="before")
    @classmethod
    def _no_file(cls, file):
        return file or None

    @field_validator("fix", mode="before")
    @classmethod
    def _no_fix(cls, fix):
        return "" if fix is None else fix

    @field_validator("file")
    @classmethod
    def _short_path(cls, file):
        return file[:500] if file else file

    @field_validator("summary")
    @classmethod
    def _short_summary(cls, summary):
        return summary[:1500]

    @field_validator("fix")
    @classmethod
    def _short_fix(cls, fix):
        return fix[:2500]


class AgentReview(BaseModel):
    """The review agent's result.json. Only the findings are read; a verdict, a band or a count it writes is ignored."""
    findings: list[AgentFinding] = []

    @field_validator("findings", mode="before")
    @classmethod
    def _at_most(cls, findings):
        if not findings:
            return []
        return findings[:MAX_FINDINGS] if isinstance(findings, list) else findings


class Finding(BaseModel):
    """A finding CI trusts: its rule is a known rule ID or NEW, its severity at least its rule's, its text redacted."""
    rule: FindingRule
    severity: Severity
    file: Optional[str] = None
    line: Optional[LineNumber] = None
    summary: str = ""
    fix: str = ""
    notes: list[str] = []  # what the checks changed (an unknown rule, a raised severity)


class PullRequest(BaseModel):
    """A pull request as the code host describes it. Its text is untrusted; its commits are checked to be object ids."""
    number: int
    title: str = ""
    body: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    baseRefName: str
    headRefName: str
    baseRefOid: Sha
    headRefOid: Sha
    isCrossRepository: bool = False

    @field_validator("author", mode="before")
    @classmethod
    def _login(cls, author):
        return author.get("login") if isinstance(author, dict) else author


class ChangeHeader(BaseModel):
    """Which change a review is about, how risky it is (from the heat map of its base), and where its config came from."""
    pr: int
    base: Sha
    head: Sha
    max_tier: Band
    max_score: Score
    model_source: str
    model_version: int
    rules_source: str
    rules_version: str
    files_changed: Count


class ReviewContext(ChangeHeader):
    """context.json of a PR review."""
    diff_patches: Count = 0
    diff_truncated: bool = False


class RiskReport(ChangeHeader):
    """risk.json of a PR review: every module the change touches, with its band, scores and evidence at the base."""
    modules: list[ModuleRisk]
    files: dict[str, Optional[str]]
    ignored: list[str]
    notes: list[str]


class PatchEntry(BaseModel):
    """One changed file of a PR and its patch (diff/index.json)."""
    file: str
    module: Optional[str]
    patch: str  # path of the patch, relative to the context folder
    patch_lines: Count
    added: Count
    removed: Count
    truncated: bool


class DiffIndex(RootModel[list[PatchEntry]]):
    pass


class Review(BaseModel):
    """The review CI trusts and posts. The verdict and the counts are derived from the findings, never taken from agent
    text; the band and score come from the precomputed risk."""
    model_config = ConfigDict(extra="ignore")  # a stored review's derived fields are recomputed, not read back

    pr: Optional[int] = None
    base: Optional[str] = None
    head: Optional[str] = None
    max_tier: Band
    max_score: Score
    rules_version: Optional[str] = None
    model_version: Optional[int] = None
    findings: list[Finding] = []
    redactions: Count = 0

    @computed_field
    @property
    def blockers(self) -> int:
        return sum(f.severity == "blocker" for f in self.findings)

    @computed_field
    @property
    def majors(self) -> int:
        return sum(f.severity == "major" for f in self.findings)

    @computed_field
    @property
    def verdict(self) -> Literal["approve", "comment", "request_changes"]:
        return "request_changes" if self.blockers else ("comment" if self.majors else "approve")


class InlineComment(BaseModel):
    path: str
    line: LineNumber
    side: Literal["RIGHT"]
    body: str


class ReviewPost(BaseModel):
    """One review as it is sent to the code host."""
    commit_id: Sha
    body: str
    event: Literal["COMMENT", "REQUEST_CHANGES"]
    comments: list[InlineComment] = []


class HostReview(BaseModel):
    """A review as the code host lists it."""
    id: Optional[int] = None
    state: Optional[str] = None
    user: Optional[str] = None
    body: str = ""

    @field_validator("user", mode="before")
    @classmethod
    def _login(cls, user):
        return user.get("login") if isinstance(user, dict) else user

    @field_validator("body", mode="before")
    @classmethod
    def _no_body(cls, body):
        return body or ""


class PostOutcome(BaseModel):
    """What posting a review did: whether it went up, and a log of each step ("info" or "warning")."""
    posted: bool = False
    log: list[tuple[Literal["info", "warning"], str]] = []
