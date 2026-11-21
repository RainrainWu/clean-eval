"""Business policy of the risk heat map: standards, weights, thresholds and level definitions.

The risk-heat-map skill's `references/rate.md` documents these numbers; change both together. Pure data and validation:
nothing here knows how files are read or results are shown. A repository overrides tunable numbers in
`.clean-eval/risk-model.json`; `Policy.from_overrides` checks every override.
"""
import re
from dataclasses import dataclass, field, fields, replace

from .errors import InvalidPolicy

# Score bands, ascending: (upper bound, name). Level n of an aspect takes the band at position 3 - n (0 Critical ... 3 Low).
BANDS = ((24, "Low"), (49, "Moderate"), (74, "High"), (100, "Critical"))
BAND_NAMES = tuple(name for _, name in BANDS)

# Level texts, most severe first (the wording of references/rate.md).
BUSINESS_LEVELS = {
    0: "root-level dependencies that can change how the whole service behaves, or critical logic with hard-to-reverse side effects",
    1: "a path on the major utility that all traffic goes through",
    2: "integration and arrangement around the core utility, to streamline the process",
    3: "logic that does not directly affect the core utility",
}
EFFECT_LEVELS = {
    0: "negative customer impact that destroys trust (payment, access control), or irreversible storage clean-up",
    1: "foundation-level resource mutation that needs a sensible period to roll back or recreate, leaving the service inaccessible",
    2: "interfacing with upstream or downstream systems, where incorrect influence propagates and needs ad-hoc fixing",
    3: "all impact stays within the service; fix and restart costs nothing painful",
}
ENTROPY_LEVELS = {
    0: "code in the shape of a non-organised process rather than reusable blocks",
    1: "utility boundaries are blurred and the data propagated is hard to trace",
    2: "lengthy functions, non-descriptive naming, or edge cases and growing data volume not handled",
    3: "well-structured with a consistent design; a new implementation finds its right place easily",
}
UNVERIFIED_LEVELS = {
    0: "only trivial tests found, or the code itself can hardly be tested",
    1: "core utility happy paths are covered by unit tests and gated in CI",
    2: "major branches and common exceptions of the utilities are well covered",
    3: "tests follow the framework's best practice, with layered suites reaching wider scope (such as e2e)",
}
# The four aspects: judgement key, score key, label, page tab id, levels. Presenters and the judgement rule read this.
ASPECTS = (
    ("business", "biz", "Business Impact", "business-impact", BUSINESS_LEVELS),
    ("effects", "effects", "Side Effects & Reversibility", "side-effects", EFFECT_LEVELS),
    ("entropy", "entropy", "Entropy", "entropy", ENTROPY_LEVELS),
    ("unverified", "unverified", "Unverified", "unverified", UNVERIFIED_LEVELS),
)
SCORE_KEYS = tuple(key for _, key, _, _, _ in ASPECTS)


def _weights(**kw):
    return field(default_factory=lambda: dict(kw))


@dataclass(frozen=True)
class Policy:
    version: int = 2
    # Modules: levels of splitting, and when a module is too small or too crowded to split.
    depth: int = 3
    max_modules: int = 60
    split_min_loc: int = 200
    split_max_files: int = 25
    exclude: tuple = ()  # globs of source paths that are not rated (vendored or generated code)
    # Paths a change may touch without carrying risk of its own (docs and images).
    ignore_paths: tuple = ("*.md", "*.rst", "docs/*", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "LICENSE*")
    # Path conventions (regular expressions over repo-relative paths).
    test_dirs: tuple = ("tests", "test", "spec", "specs", "e2e")
    test_file: str = r"(^test_.*\.py$|.*_test\.py$|^conftest\.py$)"
    tooling: str = r"(^|/)(scripts?|docs?|examples?|tools?|benchmarks?|notebooks?|fixtures?)(/|$)"
    keywords: str = (r"orchestr|pipeline|ingest|retriev|chat|agent|auth|billing|payment|gateway|handler|core|engine|router"
                     r"|scheduler|worker|consumer|api|service|search")
    migration_path: str = r"(migrations|alembic/versions)/"
    # Modules not rated for Unverified (N/A): build and infrastructure manifests, and test code itself.
    not_applicable: tuple = (
        ("Build manifest (CI workflow or pre-commit hook)", r"(^|/)(\.github|\.circleci|\.gitlab|\.pre-commit[^/]*)(/|$)"),
        ("Infrastructure manifest (terraform, kustomize, helm, k8s)", r"(^|/)(terraform|kustomize|k8s|kubernetes|helm|charts|ansible)(/|$)"),
        ("Test code", r"(^|/)(tests?|testing|fixtures?|e2e|specs?)(/|$)"),
    )
    # Overall score: half the geometric mean of impact and weakness, half (this share) the worst single aspect.
    overall_peak_share: float = 0.5
    # Band of a changed path no rated module covers (a new module, or non-Python code): unknown, not low, risk.
    unrated_band: str = "Moderate"
    # Business Impact.
    business_weights: dict = _weights(dependents=0.30, flow_weight=0.30, entry=0.15, keyword=0.10, size=0.15)
    business_saturation: dict = _weights(dependents=6, flow_weight=0.5, size=0.25)
    tooling_factor: float = 0.5
    # Side Effects & Reversibility.
    effect_points: dict = _weights(db_write=3, aws_write=2, es_write=2, http_write=1.5, fs_destructive=3, fs_write=1, subprocess=1)
    migration_points: float = 6
    effects_scale: float = 40.0
    effects_mix: dict = _weights(points=0.7, dependents=0.3)
    effects_dependents_saturation: float = 6
    # Entropy.
    entropy_weights: dict = _weights(long_funcs=0.20, longest_func=0.15, complex_funcs=0.12, avg_complexity=0.08, exceptions=0.20,
                                     untyped=0.12, coupling=0.07, big_files=0.04, todo=0.02)
    entropy_saturation: dict = _weights(long_funcs=0.15, complex_funcs=0.10, coupling=10, big_files=0.2, todo_per_k=8, exceptions_per_k=6)
    long_func_lines: int = 60
    complex_func: int = 15
    big_file_lines: int = 800
    longest_free: float = 40
    longest_range: float = 260.0
    avg_complexity_free: float = 3
    avg_complexity_range: float = 9.0
    neutral_no_functions: float = 0.3
    neutral_untyped: float = 0.5
    # Unverified.
    unverified_mix: dict = _weights(links=0.5, ratio=0.5)
    unverified_ratio_target: float = 0.8
    unverified_no_functions: float = 20.0
    single_scale_penalty: float = 5
    no_integration_penalty: float = 10
    writes_threshold: float = 40

    @classmethod
    def from_overrides(cls, overrides=None):
        """The default policy with `overrides` (a JSON object) applied and checked. Keys starting with `_` are comments."""
        base = cls()
        if not overrides:
            return base
        if not isinstance(overrides, dict):
            raise InvalidPolicy("the risk model must be a JSON object")
        known = {f.name for f in fields(cls)}
        legacy = sorted(set(overrides) & V1_KEYS)
        if legacy:
            raise InvalidPolicy("written for the v1 model (likelihood x impact from git history): %s are no longer used. The v2 "
                                "model rates four aspects; see the risk-heat-map skill's references/rate.md" % ", ".join(legacy))
        values = {}
        for key, value in overrides.items():
            if key.startswith("_") or key == "notes":
                continue
            if key not in known:
                raise InvalidPolicy("unknown risk-model key %r" % key)
            values[key] = _merge(key, getattr(base, key), value)
        policy = replace(base, **values)
        policy.validate()
        return policy

    def validate(self):
        for name in ("test_file", "tooling", "keywords", "migration_path"):
            _regex(name, getattr(self, name))
        for reason, pattern in self.not_applicable:
            _regex("not_applicable[%s]" % reason, pattern)
        if self.unrated_band not in BAND_NAMES:
            raise InvalidPolicy("unrated_band must be one of %s" % ", ".join(BAND_NAMES))
        if not 0 <= self.overall_peak_share <= 1:
            raise InvalidPolicy("overall_peak_share must be in [0, 1]")
        for name in ("business_weights", "effects_mix", "entropy_weights", "unverified_mix"):
            if sum(getattr(self, name).values()) <= 0:
                raise InvalidPolicy("%s must have a positive sum" % name)
        for name in ("depth", "max_modules", "split_min_loc", "split_max_files"):
            if getattr(self, name) < 1:
                raise InvalidPolicy("%s must be at least 1" % name)
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise InvalidPolicy("version must be an integer")
        return self


# Knobs a calibration may change on its own; the rest decide what is measured (module shape, path conventions).
STRUCTURAL = frozenset({"depth", "max_modules", "split_min_loc", "split_max_files", "exclude", "ignore_paths", "test_dirs",
                        "test_file", "tooling", "keywords", "migration_path", "not_applicable"})
TUNABLE = frozenset(f.name for f in fields(Policy)) - STRUCTURAL - {"version"}
DEFAULT = Policy()
# Keys of the retired v1 model, recognised only to explain why they no longer apply.
V1_KEYS = frozenset({"window_days", "module_roots", "module_depth", "modules", "include", "new_module_tier", "test_roots",
                     "test_file_pattern", "test_ratio_target", "fix_pattern", "revert_pattern", "fix_ratio_prior",
                     "likelihood_weights", "impact_weights", "criticality_default", "criticality", "level_thresholds"})


def _number(key, v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
        raise InvalidPolicy("%s must be a non-negative number" % key)
    return v


def _regex(key, pattern):
    try:
        re.compile(pattern)
    except (re.error, TypeError) as e:
        raise InvalidPolicy("%s is not a valid regular expression: %s" % (key, e))


def _merge(key, default, value):
    if isinstance(default, dict):
        if not isinstance(value, dict) or set(value) - set(default):
            raise InvalidPolicy("%s takes the keys %s" % (key, ", ".join(sorted(default))))
        return {**default, **{k: _number("%s.%s" % (key, k), v) for k, v in value.items()}}
    if key == "not_applicable":
        if not isinstance(value, list) or not all(isinstance(x, list) and len(x) == 2 and all(isinstance(y, str) for y in x)
                                                  for x in value):
            raise InvalidPolicy("not_applicable takes [[reason, regex], ...]")
        return tuple(tuple(x) for x in value)
    if isinstance(default, tuple):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise InvalidPolicy("%s takes a list of strings" % key)
        return tuple(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise InvalidPolicy("%s takes a string" % key)
        return value
    if isinstance(default, int) and not isinstance(default, bool) and key in ("version", "depth", "max_modules", "split_min_loc",
                                                                             "split_max_files", "long_func_lines", "complex_func",
                                                                             "big_file_lines"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidPolicy("%s takes an integer" % key)
        return value
    return _number(key, value)
