"""The risk a change carries: every module it touches, as rated at the change's base."""

from typing import Optional

from pydantic import BaseModel, model_validator

from clean_eval.domain.rating import Evidence, Facts, Scores
from clean_eval.domain.values import Band, Score


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
    def _rated_means_scored(self):
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
