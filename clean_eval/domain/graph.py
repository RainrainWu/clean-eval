"""The knowledge graph as the business rules see it: relationships between files, functions, tests and flows."""

from typing import Any

from pydantic import BaseModel


class Graph(BaseModel):
    nodes: dict[str, tuple[str, str, bool]]  # qualified name -> (kind, repo-relative file, is_test), Python only
    links: dict[str, set[str]]  # file -> files it imports or calls into (resolved edges only)
    tested: dict[str, set[str]]  # function qualified name -> test files linked to it
    flows: list[tuple[float, str, set[str]]]  # (criticality, entry file, files on the flow)
    stats: dict[str, Any]
    has_flows: bool = True  # False when the graph source cannot see execution flows
