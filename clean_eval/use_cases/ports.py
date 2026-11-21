"""Ports: what the use cases need from the outside world. Adaptors implement them; nothing here knows how."""
from typing import Optional, Protocol

from ..domain.entities import FileSignals, Heatmap, Snapshot, SourceFile
from ..domain.graph import Graph


class SourceReader(Protocol):
    def snapshot(self) -> Snapshot:
        """Which code is being read: repository name, ref, short sha and root folder."""

    def read(self) -> list:
        """The Python source files of the snapshot, as SourceFile entities (tests flagged)."""


class GraphProvider(Protocol):
    def graph_for(self, snapshot: Snapshot, files: list) -> Graph:
        """The knowledge graph of the snapshot's Python files, with repo-relative paths."""


class CodeSignals(Protocol):
    def file_signals(self, f: SourceFile) -> FileSignals:
        """Function sizes, complexity, typing, exception handling, TODOs and write calls of one file."""


class CoverageReader(Protocol):
    def line_rates(self, snapshot: Snapshot) -> dict:
        """{file path: line rate 0-1} from a coverage report in the snapshot, or {} when there is none."""


class PagePresenter(Protocol):
    def present(self, heatmap: Heatmap, suffix: str) -> str:
        """The interactive heat-map page."""


class DiagramPresenter(Protocol):
    def svg(self, heatmap: Heatmap) -> str:
        """The static treemap for a README."""

    def readme_block(self, heatmap: Heatmap, image_link: str, fingerprint: str) -> str:
        """The README block, between the heat-map markers, that shows the treemap."""


class ReportPresenter(Protocol):
    def report(self, heatmap: Heatmap) -> str:
        """A short Markdown summary: the riskiest modules and what drives them."""


class ReadmeStore(Protocol):
    def read_readme(self) -> Optional[str]:
        """The README text, or None when there is none."""

    def svg_exists(self) -> bool:
        """Whether the diagram file is already there."""

    def image_link(self) -> str:
        """Link to the diagram, relative to the README."""

    def write(self, readme_text: str, svg_text: str) -> None:
        """Write the README and the diagram."""
