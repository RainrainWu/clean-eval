"""An eval's outcome: the recommendations the eval agent proposes (untrusted), the edits of the tuning files it may make,
and what was kept. Which edits are kept is decided by the validators here (shape) and the use case (evidence)."""
import datetime as dt
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, computed_field, field_validator, model_validator

from .errors import InvalidPolicy
from .policy import TUNABLE, Policy
from .values import Count, Fraction

MAX_RECOMMENDATIONS = 30
MAX_TEXT = 2000


class PolicyEdit(BaseModel):
    """An edit of the risk-model overrides: still a valid policy, and different only in tunable knobs (weights, mixes,
    saturations, points, standards), never in what is measured (module shape, path conventions)."""
    new: dict[str, Any]
    orig: dict[str, Any] = {}

    @property
    def changed_keys(self):
        return {k for k in set(self.new) | set(self.orig)
                if not (k.startswith("_") or k == "notes") and self.new.get(k) != self.orig.get(k)}

    @model_validator(mode="after")
    def _tunable_and_valid(self):
        structural = self.changed_keys - TUNABLE - {"version"}
        if structural:
            raise ValueError("only tunable keys may change in CI; also changed: %s" % sorted(structural))
        try:
            Policy.from_overrides(self.new)
        except InvalidPolicy as e:
            raise ValueError(str(e))
        return self


class ModelChange(BaseModel):
    """An accepted edit of the risk model, with the replay AUC before and after."""
    accepted: Literal[True] = True
    changed_keys: list[str]
    auc_before: Fraction
    auc_after: Fraction


class RulesChange(BaseModel):
    accepted: Literal[True] = True


class Rejection(BaseModel):
    """An edit that was restored, and why."""
    accepted: Literal[False] = False
    reason: str


class Recommendation(BaseModel):
    target: str = ""
    change: str = ""
    evidence: str = ""

    @field_validator("target", "change", "evidence", mode="before")
    @classmethod
    def _as_text(cls, value):
        return (value if isinstance(value, str) else str(value))[:MAX_TEXT]


class AgentEvalResult(BaseModel):
    """The eval agent's result.json: its recommendations (as text, cut to size). Counts and AUC it writes are ignored."""
    recommendations: list[Recommendation] = []

    @field_validator("recommendations", mode="before")
    @classmethod
    def _objects_only(cls, recs):
        if not recs:
            return []
        return [r for r in recs if isinstance(r, dict)][:MAX_RECOMMENDATIONS] if isinstance(recs, list) else recs


class EvalResult(BaseModel):
    """The eval CI trusts: counts and AUC from the collected history, the recommendations, and which edits were kept."""
    since: dt.date
    until: dt.date
    prs: Count
    defective_prs: Count
    auc: Optional[Fraction]
    recommendations: list[Recommendation] = []
    model_change: Optional[Union[ModelChange, Rejection]] = None
    rules_change: Optional[Union[RulesChange, Rejection]] = None
    other_files_reverted: list[str] = []
    redactions: Count = 0

    @computed_field
    @property
    def applied(self) -> bool:
        return any(c is not None and c.accepted for c in (self.model_change, self.rules_change))
