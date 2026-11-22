"""The risk policy: the risk model's defaults, the overrides a repository keeps in .clean-eval/risk-model.json,
and the value types and score bands every model uses."""

import json
import re
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    Optional,
    Self,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from clean_eval.domain.errors import InvalidPolicy

# Value types shared by the data models: constrained scalars whose rules pydantic enforces wherever they are used.


class Band(StrEnum):
    """A score band, from the lowest risk. Level n of an aspect takes the band of rank 3 - n (0 Critical ... 3 Low)."""

    LOW = "Low"
    MODERATE = "Moderate"
    HIGH = "High"
    CRITICAL = "Critical"

    @property
    def rank(
        self,
    ) -> int:
        """Its place among the bands, from 0 for the lowest risk."""
        return list(Band).index(self)


# Each band's upper bound, ascending: a score falls in the first band whose bound it doesn't exceed.
BANDS: tuple[tuple[int, Band], ...] = (
    (24, Band.LOW),
    (49, Band.MODERATE),
    (74, Band.HIGH),
    (100, Band.CRITICAL),
)


class Severity(StrEnum):
    """A review finding's severity, from the mildest."""

    MINOR = "minor"
    MAJOR = "major"
    BLOCKER = "blocker"

    @property
    def rank(
        self,
    ) -> int:
        """Its place among the severities, from 0 for the mildest."""
        return list(Severity).index(self)


Score = Annotated[
    int,
    Field(
        ge=0,
        le=100,
    ),
]  # 0-100, as the bands read it
Level = Annotated[
    int,
    Field(
        ge=0,
        le=3,
        strict=True,
    ),
]  # 0 most severe ... 3 least
Count = Annotated[
    int,
    Field(
        ge=0,
    ),
]
Fraction = Annotated[
    float,
    Field(
        ge=0,
        le=1,
    ),
]
Sha = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$",
    ),
]  # a full git object id (SHA-1 or SHA-256)
OneLine = Annotated[
    str,
    Field(
        pattern=r"^[^\r\n]*$",
    ),
]

# Numbers of the policy: JSON numbers only (a bool or a string is refused), finite.
Number = Annotated[
    float,
    Field(
        ge=0,
        strict=True,
        allow_inf_nan=False,
    ),
]
Divisor = Annotated[
    float,
    Field(
        gt=0,
        strict=True,
        allow_inf_nan=False,
    ),
]  # divides a measurement: never 0
Whole = Annotated[
    int,
    Field(
        ge=0,
        strict=True,
    ),
]
AtLeastOne = Annotated[
    int,
    Field(
        ge=1,
        strict=True,
    ),
]


# Business policy of the risk heat map: standards, weights, thresholds and level definitions, as one validated model.
#
# The risk-heat-map skill's `references/rate.md` documents these numbers; change both together. A repository overrides
# tunable numbers in `.clean-eval/risk-model.json`; `Policy.from_overrides` checks every override with the validators
# below (types and ranges on the fields, the cross-field rules on the model).

# Where a repository keeps its own tuning (git paths, so always "/"-separated).
REPO_DIR = ".clean-eval"
REPO_POLICY = REPO_DIR + "/risk-model.json"
REPO_RULES = REPO_DIR + "/review-rules.md"

# Level texts, most severe first (the wording of references/rate.md).
BUSINESS_LEVELS = frozendict(
    {
        0: "root-level dependencies that can change how the whole service behaves, or critical logic with hard-to-reverse side effects",
        1: "a path on the major utility that all traffic goes through",
        2: "integration and arrangement around the core utility, to streamline the process",
        3: "logic that does not directly affect the core utility",
    }
)
EFFECT_LEVELS = frozendict(
    {
        0: "negative customer impact that destroys trust (payment, access control), or irreversible storage clean-up",
        1: "foundation-level resource mutation that needs a sensible period to roll back or recreate, leaving the service inaccessible",
        2: "interfacing with upstream or downstream systems, where incorrect influence propagates and needs ad-hoc fixing",
        3: "all impact stays within the service; fix and restart costs nothing painful",
    }
)
ENTROPY_LEVELS = frozendict(
    {
        0: "code in the shape of a non-organised process rather than reusable blocks",
        1: "utility boundaries are blurred and the data propagated is hard to trace",
        2: "lengthy functions, non-descriptive naming, or edge cases and growing data volume not handled",
        3: "well-structured with a consistent design; a new implementation finds its right place easily",
    }
)
UNVERIFIED_LEVELS = frozendict(
    {
        0: "only trivial tests found, or the code itself can hardly be tested",
        1: "core utility happy paths are covered by unit tests and gated in CI",
        2: "major branches and common exceptions of the utilities are well covered",
        3: "tests follow the framework's best practice, with layered suites reaching wider scope (such as e2e)",
    }
)


class Aspect(StrEnum):
    """The four aspects a module is rated on, each by the key a judgement levels it under; the presenters and the
    judgement rule read their score key, label and level texts."""

    BUSINESS = "business"
    EFFECTS = "effects"
    ENTROPY = "entropy"
    UNVERIFIED = "unverified"

    @property
    def score_key(
        self,
    ) -> str:
        """Its field in Scores: `biz` for Business Impact, else its own key."""
        return "biz" if self is Aspect.BUSINESS else self.value

    @property
    def label(
        self,
    ) -> str:
        """Its name on the heat map."""
        return ASPECT_LABELS[self]

    @property
    def levels(
        self,
    ) -> frozendict[int, str]:
        """What each of its levels means, from 0, the riskiest, to 3."""
        return ASPECT_LEVELS[self]


ASPECT_LABELS = frozendict(
    {
        Aspect.BUSINESS: "Business Impact",
        Aspect.EFFECTS: "Side Effects & Reversibility",
        Aspect.ENTROPY: "Entropy",
        Aspect.UNVERIFIED: "Unverified",
    }
)
ASPECT_LEVELS = frozendict(
    {
        Aspect.BUSINESS: BUSINESS_LEVELS,
        Aspect.EFFECTS: EFFECT_LEVELS,
        Aspect.ENTROPY: ENTROPY_LEVELS,
        Aspect.UNVERIFIED: UNVERIFIED_LEVELS,
    }
)
# Weight groups: each mixes the terms of one aspect, so it needs a positive sum.
WEIGHT_GROUPS = ("business_weights", "effects_mix", "entropy_weights", "unverified_mix")
REGEX_KNOBS = ("test_file", "tooling", "keywords", "migration_path")
# Keys of the retired v1 model, recognised only to explain why they no longer apply.
V1_KEYS = frozenset(
    {
        "window_days",
        "module_roots",
        "module_depth",
        "modules",
        "include",
        "new_module_tier",
        "test_roots",
        "test_file_pattern",
        "test_ratio_target",
        "fix_pattern",
        "revert_pattern",
        "fix_ratio_prior",
        "likelihood_weights",
        "impact_weights",
        "criticality_default",
        "criticality",
        "level_thresholds",
    }
)


class Policy(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )

    version: Whole = 2
    # Modules: levels of splitting, and when a module is too small or too crowded to split.
    depth: AtLeastOne = 3
    max_modules: AtLeastOne = 60
    split_min_loc: AtLeastOne = 200
    split_max_files: AtLeastOne = 25
    exclude: tuple[str, ...] = ()  # globs of source paths that are not rated (vendored or generated code)
    # Paths a change may touch without carrying risk of its own: documentation and images. Not every Markdown file:
    # agent instructions (SKILL.md, CLAUDE.md, AGENTS.md) and review rules steer behaviour, so they stay risky.
    ignore_paths: tuple[str, ...] = (
        "README*",
        "*/README*",
        "CHANGELOG*",
        "*/CHANGELOG*",
        "LICENSE*",
        "*/LICENSE*",
        "docs/*",
        "*.png",
        "*.jpg",
        "*.jpeg",
        "*.gif",
        "*.svg",
    )
    # Path conventions (regular expressions over repo-relative paths).
    test_dirs: tuple[str, ...] = ("tests", "test", "spec", "specs", "e2e")
    test_file: str = r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$)"
    tooling: str = r"(^|/)(scripts?|docs?|examples?|tools?|benchmarks?|notebooks?|fixtures?)(/|$)"
    keywords: str = (
        r"orchestr|pipeline|ingest|retriev|chat|agent|auth|billing|payment|gateway|handler|core|engine|router"
        r"|scheduler|worker|consumer|api|service|search"
    )
    migration_path: str = r"(migrations|alembic/versions)/"
    # Modules not rated for Unverified (N/A): build and infrastructure manifests, and test code itself.
    not_applicable: tuple[tuple[str, str], ...] = (
        (
            "Build manifest (CI workflow or pre-commit hook)",
            r"(^|/)(\.github|\.circleci|\.gitlab|\.pre-commit[^/]*)(/|$)",
        ),
        (
            "Infrastructure manifest (terraform, kustomize, helm, k8s)",
            r"(^|/)(terraform|kustomize|k8s|kubernetes|helm|charts|ansible)(/|$)",
        ),
        ("Test code", r"(^|/)(tests?|testing|fixtures?|e2e|specs?)(/|$)"),
    )
    # Overall score: half the geometric mean of impact and weakness, half (this share) the worst single aspect.
    overall_peak_share: Fraction = Field(
        0.5,
        strict=True,
        allow_inf_nan=False,
    )
    # Band of a changed path no rated module covers (a new module, or non-Python code): unknown, not low, risk.
    unrated_band: Band = Band.MODERATE
    # Business Impact.
    business_weights: dict[str, Number] = Field(
        default_factory=lambda: dict(dependents=0.30, keyword=0.10, size=0.15),
    )
    business_saturation: dict[str, Divisor] = Field(
        default_factory=lambda: dict(dependents=6, size=0.25),
    )
    tooling_factor: Number = 0.5
    # Side Effects & Reversibility.
    effect_points: dict[str, Number] = Field(
        default_factory=lambda: dict(
            db_write=3, aws_write=2, es_write=2, http_write=1.5, fs_destructive=3, fs_write=1, subprocess=1
        )
    )
    migration_points: Number = 6
    effects_scale: Divisor = 40.0
    effects_mix: dict[str, Number] = Field(
        default_factory=lambda: dict(points=0.7, dependents=0.3),
    )
    effects_dependents_saturation: Divisor = 6
    # Entropy.
    entropy_weights: dict[str, Number] = Field(
        default_factory=lambda: dict(
            long_funcs=0.20,
            longest_func=0.15,
            complex_funcs=0.12,
            avg_complexity=0.08,
            exceptions=0.20,
            untyped=0.12,
            coupling=0.07,
            big_files=0.04,
            todo=0.02,
        )
    )
    entropy_saturation: dict[str, Divisor] = Field(
        default_factory=lambda: dict(
            long_funcs=0.15, complex_funcs=0.10, coupling=10, big_files=0.2, todo_per_k=8, exceptions_per_k=6
        )
    )
    long_func_lines: Whole = 60
    complex_func: Whole = 15
    big_file_lines: Whole = 800
    longest_free: Number = 40
    longest_range: Divisor = 260.0
    avg_complexity_free: Number = 3
    avg_complexity_range: Divisor = 9.0
    neutral_no_functions: Number = 0.3
    neutral_untyped: Number = 0.5
    # Unverified.
    unverified_mix: dict[str, Number] = Field(
        default_factory=lambda: dict(links=0.5, ratio=0.5),
    )
    unverified_ratio_target: Divisor = 0.8
    unverified_no_functions: Number = 20.0
    single_scale_penalty: Number = 5
    no_integration_penalty: Number = 10
    writes_threshold: Number = 40

    @model_validator(mode="before")
    @classmethod
    def merge_overrides(
        cls,
        data: object,
    ) -> dict[str, Any]:
        """Overrides are partial: comments (`_*`, `notes`) are dropped, v1 keys are refused with a migration hint, and a
        dictionary knob keeps the defaults of the keys it doesn't name; naming a key it doesn't have is an error."""
        if not isinstance(data, dict):
            raise ValueError(
                "the risk model must be a JSON object",
            )
        data = {k: v for k, v in data.items() if not (isinstance(k, str) and (k.startswith("_") or k == "notes"))}
        legacy = sorted(set(data) & V1_KEYS)
        if legacy:
            raise ValueError(
                "written for the v1 model (likelihood x impact from git history): %s are no longer used. The v2 "
                "model rates four aspects; see the risk-heat-map skill's references/rate.md" % ", ".join(legacy)
            )
        for key, value in data.items():
            field = cls.model_fields.get(key)
            default = field.get_default(call_default_factory=True) if field else None
            if isinstance(default, dict):
                if not isinstance(value, dict) or set(value) - set(default):
                    raise ValueError(
                        "%s takes the keys %s" % (key, ", ".join(sorted(default))),
                    )
                data[key] = {**default, **value}
        return data

    @field_validator(*REGEX_KNOBS)
    @classmethod
    def check_patterns_compile(
        cls,
        pattern: str,
    ) -> str:
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(
                "not a valid regular expression: %s" % e,
            )
        return pattern

    @field_validator("not_applicable")
    @classmethod
    def check_reasons_compile(
        cls,
        rules: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        for reason, pattern in rules:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(
                    "%r is not a valid regular expression: %s" % (reason, e),
                )
        return rules

    @model_validator(mode="after")
    def check_weights_mix_something(
        self,
    ) -> Self:
        for name in WEIGHT_GROUPS:
            if sum(getattr(self, name).values()) <= 0:
                raise ValueError(
                    "%s must have a positive sum" % name,
                )
        return self

    @classmethod
    def from_overrides(
        cls,
        overrides: Optional[dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> Self:
        """The default policy with `overrides` (a JSON object) applied; InvalidPolicy tells what the validators refused."""
        try:
            return cls.model_validate({} if overrides is None else overrides)
        except ValidationError as e:
            raise InvalidPolicy.from_validation(e, source)

    @classmethod
    def from_text(
        cls,
        text: str,
        source: str,
    ) -> Self:
        """The policy an overrides file describes (empty text: the defaults); `source` names the file in errors."""
        try:
            overrides = json.loads(text) if text and text.strip() else {}
        except ValueError as e:
            raise InvalidPolicy(
                "%s is not valid JSON: %s" % (source, e),
            )
        return cls.from_overrides(overrides, source)


# Knobs a calibration may change on its own; the rest decide what is measured (module shape, path conventions).
STRUCTURAL = frozenset(
    {
        "depth",
        "max_modules",
        "split_min_loc",
        "split_max_files",
        "exclude",
        "ignore_paths",
        "test_dirs",
        "test_file",
        "tooling",
        "keywords",
        "migration_path",
        "not_applicable",
    }
)
TUNABLE = frozenset(Policy.model_fields) - STRUCTURAL - {"version"}
DEFAULT = Policy()
