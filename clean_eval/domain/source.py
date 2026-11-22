"""What is read from the code: a snapshot of a repository, its source files, and the signals one file gives."""

from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field

from clean_eval.domain.values import Count


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
