"""Value types shared by the data models: constrained scalars whose rules pydantic enforces wherever they are used."""

from typing import Annotated, Literal

from pydantic import Field

# Score bands, ascending: (upper bound, name). Level n of an aspect takes the band at position 3 - n (0 Critical ... 3 Low).
BANDS = ((24, "Low"), (49, "Moderate"), (74, "High"), (100, "Critical"))
BAND_NAMES = tuple(name for _, name in BANDS)
Band = Literal["Low", "Moderate", "High", "Critical"]

SEVERITIES = ("minor", "major", "blocker")  # ascending
Severity = Literal["minor", "major", "blocker"]

Score = Annotated[int, Field(ge=0, le=100)]  # 0-100, as the bands read it
Level = Annotated[int, Field(ge=0, le=3, strict=True)]  # 0 most severe ... 3 least
Count = Annotated[int, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")]  # a full git object id (SHA-1 or SHA-256)
OneLine = Annotated[str, Field(pattern=r"^[^\r\n]*$")]

# Numbers of the policy: JSON numbers only (a bool or a string is refused), finite.
Number = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]
Divisor = Annotated[float, Field(gt=0, strict=True, allow_inf_nan=False)]  # divides a measurement: never 0
Whole = Annotated[int, Field(ge=0, strict=True)]
AtLeastOne = Annotated[int, Field(ge=1, strict=True)]
