"""A reviewer's judgement of a heat map: one level (0 most severe ... 3 least) per module and aspect, with a reason."""
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator, model_validator

from .values import Level

AspectName = Literal["business", "effects", "entropy", "unverified"]
MAX_REASON = 240


class AspectLevel(BaseModel):
    level: Level
    reason: str = ""

    @field_validator("reason", mode="before")
    @classmethod
    def _short_text(cls, reason):
        return ("" if reason is None else str(reason))[:MAX_REASON]


class Judgement(BaseModel):
    """{"modules": {name: {aspect: {"level": 0-3, "reason": ...}}}}. The levelling agent's output is untrusted: an entry
    that doesn't validate is dropped rather than failing the whole judgement, as the skill documents."""
    modules: dict[str, dict[AspectName, AspectLevel]] = {}

    @model_validator(mode="before")
    @classmethod
    def _keep_valid_entries(cls, data):
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
