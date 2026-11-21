"""Ports: what the use cases need from the outside world. Adaptors implement them; nothing here knows how."""
from typing import Optional, Protocol

from ..domain.entities import FileSignals, Heatmap, Snapshot, SourceFile
from ..domain.graph import Graph
from ..domain.policy import Policy


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


# ---------------------------------------------------------------- the CI agent's run (prepare, finalize, post)

class Folder(Protocol):
    """One folder of a CI run: the precomputed context, the agent's output, or the final output. Names are relative."""

    def exists(self, name: str) -> bool:
        """Whether the file is there."""

    def read_text(self, name: str) -> str:
        """The file's text; HeatmapError when it can't be read."""

    def read_json(self, name: str):
        """The parsed file; HeatmapError when it can't be read or parsed."""

    def write_text(self, name: str, text: str) -> None:
        """Write the file, making its folders."""

    def write_json(self, name: str, obj) -> None:
        """Write the object as JSON."""


class Repository(Protocol):
    """The git history of the repository under review."""

    def ensure_commit(self, sha: str, refspec: str) -> None:
        """Fetch `refspec` when `sha` is missing; HeatmapError when it still is."""

    def merge_base(self, a: str, b: str) -> str:
        """The best common ancestor of two revisions."""

    def show(self, rev: str, path: str) -> Optional[str]:
        """The file `path` at `rev`, or None when there is none."""

    def diff(self, base: str, head: str, path: Optional[str] = None) -> str:
        """The patch base..head of one path, or of all of them (renames detected)."""

    def diff_files(self, base: str, head: str) -> list:
        """The paths changed between two revisions."""

    def file_log(self, rev: str, path: str, limit: int) -> str:
        """One line per commit that touched `path`, up to `rev`, newest first."""

    def commit_patch(self, sha: str, paths: list) -> str:
        """A commit's message, stat and patch, limited to `paths`."""

    def default_branch(self) -> str:
        """The branch PRs are merged into."""

    def first_parent_log(self, branch: str, since: str, until: str) -> list:
        """[(sha, [parent shas], author, unix time, subject)] of the first-parent commits of `branch` in the period."""


class RevisionRisk(Protocol):
    """The heat map of any revision, rated from git objects."""

    def heatmap_at(self, rev: str, policy: Policy) -> Heatmap:
        """The heat map of `rev`; NoPythonSource when it has no Python source."""

    def assess(self, base: str, head: str, policy: Policy) -> tuple:
        """(heat map at base, ChangeRisk of base..head)."""


class CodeHost(Protocol):
    """The pull requests of the repository on its code host. Failures raise RemoteError."""

    def pull_request(self, number: int) -> dict:
        """Title, body, author, URL, base and head refs and commits of a PR."""

    def require_auth(self) -> None:
        """HeatmapError unless the host can be called."""

    def review_threads(self, number: int) -> tuple:
        """(the PR with its reviews, comments, labels and size; its inline review comments)."""

    def post_review(self, number: int, payload: dict) -> dict:
        """Post one review; the review as posted."""

    def reviews(self, number: int) -> list:
        """Every review of the PR."""

    def dismiss_review(self, number: int, review_id: int, message: str) -> None:
        """Dismiss one review."""


class TuningFiles(Protocol):
    """The repository's tuning files (`.clean-eval/`), and a snapshot of them taken before the agent ran."""

    def snapshot(self) -> None:
        """Copy the tuning files aside, as they are now."""

    def current(self, name: str) -> Optional[str]:
        """The text of `.clean-eval/<name>` now, or None when there is no such file."""

    def original(self, name: str) -> Optional[str]:
        """The text of `.clean-eval/<name>` in the snapshot, or None when there was no such file."""

    def restore(self, name: str) -> None:
        """Put `.clean-eval/<name>` back as it was in the snapshot (removed when it wasn't there)."""

    def restore_others(self, keep) -> list:
        """Put every file under `.clean-eval/` except `keep` back as it was; the paths that needed it, sorted."""
