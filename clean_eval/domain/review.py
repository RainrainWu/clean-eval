"""The PR review in CI: the pull request, what the agent proposes, the review CI derives, posts and reports, and
a CI run's step outputs, secrets, task and folders."""

import re
from enum import StrEnum
from typing import (
    Annotated,
    Optional,
    Self,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    computed_field,
    field_validator,
    model_validator,
)

from clean_eval.domain.policy import (
    Band,
    Count,
    OneLine,
    Score,
    Severity,
    Sha,
)
from clean_eval.domain.rating import ModuleRisk

# A PR review: the rules it applies, the findings the review agent proposes (untrusted, checked field by field), the
# review CI trusts and posts, and the PR and change it is about.

RuleId = Annotated[
    str,
    Field(
        pattern=r"^[A-Z]{1,4}-\d{2,3}$",
    ),
]
FindingRule = Annotated[
    str,
    Field(
        pattern=r"^(?:[A-Z]{1,4}-\d{2,3}|NEW)$",
    ),
]  # NEW: a problem no rule covers
LineNumber = Annotated[
    int,
    Field(
        ge=1,
        strict=True,
    ),
]
RulePattern = Annotated[
    str,
    Field(
        pattern=r"^[A-Z0-9?*\[\]!-]+$",
    ),
]  # a rule ID (CA-01), or a glob of IDs (CA-*, Q-0?)
LEGACY_BANDS = frozendict({"Medium": Band.MODERATE})  # "Medium" is the pre-v2 name of the Moderate band
MAX_FINDINGS = 50


class Verdict(StrEnum):
    """A review's verdict, derived from its findings: request changes on a blocker, comment on a major, else approve."""

    APPROVE = "approve"
    COMMENT = "comment"
    REQUEST_CHANGES = "request_changes"


class LogLevel(StrEnum):
    """How a line of a run's log reads: information, or a warning."""

    INFO = "info"
    WARNING = "warning"


class ReviewRule(BaseModel):
    """One row of a rules table: `| ID | severity | min tier | rule |`, with the rule's title (its bold lead)."""

    id: RuleId
    severity: Severity
    min_tier: Band
    title: str = ""

    @field_validator("min_tier", mode="before")
    @classmethod
    def map_legacy_band(
        cls,
        tier: object,
    ) -> object:
        # rules files copied from the pre-v2 default keep working
        return LEGACY_BANDS.get(tier, tier) if isinstance(tier, str) else tier


class RulesTable(BaseModel):
    """The rules of a rules file, as a review reads them (a later row with the same ID wins)."""

    rules: list[ReviewRule] = []

    @property
    def by_id(
        self,
    ) -> dict[str, ReviewRule]:
        return {r.id: r for r in self.rules}


class CheckedRules(RulesTable):
    """Rules an unattended retro may keep: at least one rule row, and no ID twice."""

    @model_validator(mode="after")
    def check_usable(
        self,
    ) -> Self:
        if not self.rules:
            raise ValueError(
                "the rules have no rule rows",
            )
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(
                "duplicate rule IDs: %s" % ", ".join(sorted({i for i in ids if ids.count(i) > 1})),
            )
        return self


class RuleSelection(BaseModel):
    """Which rules a review applies (`.clean-eval/review.json`): only those `include` names, else every rule but those
    `exclude` names (include supersedes exclude). Each name is a rule ID (`CA-01`) or a glob of IDs (`CA-*`)."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    include: tuple[RulePattern, ...] = ()
    exclude: tuple[RulePattern, ...] = ()


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
    def empty_file_is_none(
        cls,
        file: object,
    ) -> object:
        return file or None

    @field_validator("fix", mode="before")
    @classmethod
    def none_fix_is_empty(
        cls,
        fix: object,
    ) -> object:
        return "" if fix is None else fix

    @field_validator("file")
    @classmethod
    def shorten_path(
        cls,
        file: Optional[str],
    ) -> Optional[str]:
        return file[:500] if file else file

    @field_validator("summary")
    @classmethod
    def shorten_summary(
        cls,
        summary: str,
    ) -> str:
        return summary[:1500]

    @field_validator("fix")
    @classmethod
    def shorten_fix(
        cls,
        fix: str,
    ) -> str:
        return fix[:2500]


class AgentReview(BaseModel):
    """The review agent's result.json. Only the findings are read; a verdict, a band or a count it writes is ignored."""

    findings: list[AgentFinding] = []

    @field_validator("findings", mode="before")
    @classmethod
    def cap_findings(
        cls,
        findings: object,
    ) -> object:
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


class RuleViolation(BaseModel):
    """A rule a change violates: its ID and title, the most severe of the findings that cite it, and those findings."""

    rule: RuleId
    title: str = ""
    severity: Severity
    findings: list[Finding]


class ReviewComment(BaseModel):
    """What a review's comment says, in the one layout every review comment has: the verdict; the risk the review was
    scaled by (the max band and score, and the modules touched); how many of the rules it checked; the rules violated,
    most severe first, then by ID; the problems no rule covers; and the reviewer's notes."""

    verdict: Verdict
    max_tier: Band
    max_score: Score
    modules: list[ModuleRisk] = []
    rules_checked: Count = 0
    rules_total: Count = 0
    violations: list[RuleViolation] = []
    uncovered: list[Finding] = []
    notes: str = ""

    @property
    def severity_counts(
        self,
    ) -> dict[Severity, int]:
        """How many findings of each severity, the most severe first."""
        found = [f for violation in self.violations for f in violation.findings] + self.uncovered
        return {severity: sum(f.severity == severity for f in found) for severity in reversed(Severity)}


class PullRequest(BaseModel):
    """A pull request as the code host describes it: its title, body and author (untrusted text), its URL, its base and
    head branches and commits, whether its head is in another repository, and the ref its head is fetched from
    (`head_refspec`). The commits are checked to be object ids."""

    number: int
    title: str = ""
    body: Optional[str] = None
    author: Optional[str] = None
    url: Optional[str] = None
    base_ref: str
    head_ref: str
    base_sha: Sha
    head_sha: Sha
    cross_repository: bool = False
    head_refspec: str


class ChangeHeader(BaseModel):
    """Which change a review is about, how risky it is (from the heat map of its base), and where its config is from."""

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
    """context.json of a PR review, with notes on its rules (a file or selection ignored, a rule ID defined twice, a
    selected name that matches no rule)."""

    diff_patches: Count = 0
    diff_truncated: bool = False
    rules_notes: list[str] = []


class RiskReport(ChangeHeader):
    """risk.json of a PR review: every module the change touches, with its band, scores and evidence at the base."""

    modules: list[ModuleRisk]
    files: dict[str, Optional[str]]
    ignored: list[str]
    notes: list[str]


class FileDiff(BaseModel):
    """One changed file's diff: its patch, the lines it adds and removes, and the lines of the new file that its hunks
    show (`hunks`: first and last of each), the only ones an inline comment can go on."""

    path: str
    patch: str
    added: Count
    removed: Count
    hunks: list[tuple[int, int]] = []


class PatchEntry(BaseModel):
    """One changed file of a PR and its patch (diff/index.json)."""

    file: str
    module: Optional[str]
    patch: str  # path of the patch, relative to the context folder
    patch_lines: Count
    added: Count
    removed: Count
    truncated: bool
    hunks: list[tuple[int, int]] = []  # the lines of the changed file its diff shows (FileDiff's)


class DiffIndex(RootModel[list[PatchEntry]]):
    pass


class Review(BaseModel):
    """The review CI trusts and posts. The verdict and the counts are derived from the findings, never taken from agent
    text; the band and score come from the precomputed risk."""

    model_config = ConfigDict(
        extra="ignore",
    )  # a stored review's derived fields are recomputed, not read back

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
    def blockers(
        self,
    ) -> int:
        return sum(f.severity == Severity.BLOCKER for f in self.findings)

    @computed_field
    @property
    def majors(
        self,
    ) -> int:
        return sum(f.severity == Severity.MAJOR for f in self.findings)

    @computed_field
    @property
    def verdict(
        self,
    ) -> Verdict:
        return Verdict.REQUEST_CHANGES if self.blockers else (Verdict.COMMENT if self.majors else Verdict.APPROVE)


class InlineComment(BaseModel):
    """A review's comment on one line of a changed file, as the change leaves it."""

    path: str
    line: LineNumber
    body: str


class ReviewPost(BaseModel):
    """A review to post on commit `commit`: its body, whether it requests changes (else it comments: the action never
    approves a PR), and its inline comments."""

    commit: Sha
    body: str
    request_changes: bool = False
    comments: list[InlineComment] = []


class HostReview(BaseModel):
    """A review as the code host lists it: its id, its author, whether it requests changes, and its body."""

    id: Optional[int] = None
    author: Optional[str] = None
    requests_changes: bool = False
    body: str = ""


class PostAttempt(BaseModel):
    """One attempt at posting a review: what it tried, in the code host's terms, and why the host refused it (None when
    it took it)."""

    tried: str
    error: Optional[str] = None


class ReviewPosting(BaseModel):
    """What the code host did with a review: each attempt in turn, and the review as posted (None when it refused them
    all)."""

    attempts: list[PostAttempt] = []
    review: Optional[HostReview] = None


class PostOutcome(BaseModel):
    """What posting a review did, as the run reports it: whether it went up, and a log line per step."""

    posted: bool = False
    log: list[tuple[LogLevel, str]] = []


# What crosses out of the CI runner around its agent: the step outputs, and the secrets cleaned from everything that
# leaves it.

OutputName = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9-]*$",
    ),
]
OutputValue = Annotated[
    str,
    Field(
        pattern=r"^[^\r\n]*$",
    ),
]  # one `name=value` line: a line break would start another


class StepOutputs(RootModel[dict[OutputName, OutputValue]]):
    """A CI step's outputs, one line each: enums and numbers clean-eval built, never agent text."""


class Secrets(BaseModel):
    """What text that leaves the CI runner is cleaned of: secret values, and the patterns of well-known credentials. A
    pattern with a group stands for the group alone (an authorization header's credential, not the header)."""

    model_config = ConfigDict(
        frozen=True,
    )

    values: tuple[str, ...] = ()
    patterns: tuple[re.Pattern[str], ...] = ()


# A CI run of a skill (the `action` command): the skill, its folders, the task it gives the agent, and what running the
# agent gave.


class Skill(StrEnum):
    """The skills a CI run runs: what `action` and `finalize --skill` take."""

    PR_REVIEW = "pr-review"
    RETRO = "retro"
    RISK_HEAT_MAP = "risk-heat-map"


class RunFolders(BaseModel):
    """The folders of a CI run, as real absolute paths: the workspace (the checkout the skill runs on), the output
    folder, and in it the context the agent reads (precomputed, read-only to it) and the agent's own folder, the only
    one it may write."""

    model_config = ConfigDict(
        frozen=True,
    )

    workspace: str
    out: str
    context: str
    agent: str


class AgentTask(BaseModel):
    """What a CI run asks of its agent: the skill to follow, what to do (`goal`, a sentence), the context to read
    (`inputs`, paths in the context folder, with a note in parentheses where needed) and the files to write (`outputs`,
    paths in the agent's folder). How the task is put to the agent is the agent's business."""

    model_config = ConfigDict(
        frozen=True,
    )

    skill: Skill
    goal: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


class AgentRun(BaseModel):
    """What running the agent gave: its exit status, and its transcript (what it printed)."""

    status: int
    transcript: str
