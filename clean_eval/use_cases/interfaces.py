"""The abstract classes of every external utility the business logic uses. The use-case functions take them as
parameters and call only their abstract methods, so they never know (or import) what is behind them; the outer layers
inherit and implement them:

  Git, CodeHost, FileSystem, KnowledgeGraph, Environment, Clock   the external layer's gateways (external/*.py)
  DiagramPresenter, ReportPresenter                              the adaptor layer's presenters (rendering only)
"""

from abc import ABC, abstractmethod
from typing import Optional

from clean_eval.domain.rating import Graph, Heatmap
from clean_eval.domain.review import HostReview, PullRequest, ReviewPost


class Git(ABC):
    """A git repository: the sources of its working tree and revisions, its history, diffs and patches."""

    @abstractmethod
    def toplevel(self) -> str:
        """The repository's root folder."""

    @abstractmethod
    def rev_parse(self, rev: str, short: bool = False) -> str:
        """The commit `rev` names (short: abbreviated)."""

    @abstractmethod
    def has_commit(self, rev: str) -> bool:
        """Whether the commit is here."""

    @abstractmethod
    def merge_base(self, a: str, b: str) -> str:
        """The best common ancestor of two revisions."""

    @abstractmethod
    def branch(self) -> str:
        """The checked-out branch ("" when detached)."""

    @abstractmethod
    def repo_name(self) -> str:
        """The repository's name (from its origin, else its folder)."""

    @abstractmethod
    def ls_files(self) -> list:
        """The tracked paths of the working tree."""

    @abstractmethod
    def ls_tree(self, rev: str) -> list:
        """[(path, blob id, size)] of the files at `rev`."""

    @abstractmethod
    def read_blobs(self, blob_ids: list) -> dict:
        """{blob id: text} of the given blobs."""

    @abstractmethod
    def diff_files(self, base: str, head: str) -> list:
        """The paths changed between two revisions."""

    @abstractmethod
    def show(self, rev: str, path: str) -> Optional[str]:
        """The file `path` at `rev`, or None when there is none."""

    @abstractmethod
    def ensure_commit(self, sha: str, refspec: str) -> None:
        """Fetch `refspec` when `sha` is missing; HeatmapError when it still is."""

    @abstractmethod
    def diff(self, base: str, head: str, path: Optional[str] = None) -> str:
        """The patch base..head of one path, or of all of them (renames detected)."""

    @abstractmethod
    def file_log(self, rev: str, path: str, limit: int = 10) -> str:
        """One line per commit that touched `path`, up to `rev`, newest first."""

    @abstractmethod
    def commit_patch(self, sha: str, paths: list) -> str:
        """A commit's message, stat and patch, limited to `paths`."""

    @abstractmethod
    def default_branch(self) -> str:
        """The branch PRs are merged into."""

    @abstractmethod
    def first_parent_log(self, branch: str, since: str, until: str) -> list:
        """[(sha, [parent shas], author, unix time, subject)] of the first-parent commits of `branch` in the period."""


class CodeHost(ABC):
    """The pull requests of the repository on its code host. Failures raise RemoteError."""

    @abstractmethod
    def pull_request(self, number: int) -> PullRequest:
        """A PR: its title, body, author, URL, and base and head refs and commits."""

    @abstractmethod
    def require_auth(self) -> None:
        """HeatmapError unless the host can be called."""

    @abstractmethod
    def review_threads(self, number: int) -> tuple:
        """(the PR with its reviews, comments, labels and size; its inline review comments), as the host gives them."""

    @abstractmethod
    def post_review(self, number: int, post: ReviewPost) -> HostReview:
        """Post one review; the review as posted."""

    @abstractmethod
    def reviews(self, number: int) -> "list[HostReview]":
        """Every review of the PR."""

    @abstractmethod
    def dismiss_review(self, number: int, review_id: int, message: str) -> None:
        """Dismiss one review."""


class FileSystem(ABC):
    """Files and folders, and the path strings that name them."""

    sep: str  # the path separator

    @abstractmethod
    def join(self, *parts: str) -> str: ...

    @abstractmethod
    def parent(self, path: str) -> str: ...

    @abstractmethod
    def name(self, path: str) -> str: ...

    @abstractmethod
    def relative(self, path: str, start: str) -> str: ...

    @abstractmethod
    def absolute(self, path: str) -> str: ...

    @abstractmethod
    def real_path(self, path: str) -> str:
        """The path with every symlink resolved."""

    @abstractmethod
    def expand_user(self, path: str) -> str: ...

    @abstractmethod
    def cwd(self) -> str: ...

    @abstractmethod
    def exists(self, path: str) -> bool: ...

    @abstractmethod
    def lexists(self, path: str) -> bool:
        """Whether anything is at `path`, a dangling symlink included."""

    @abstractmethod
    def is_file(self, path: str) -> bool: ...

    @abstractmethod
    def is_dir(self, path: str) -> bool: ...

    @abstractmethod
    def is_link(self, path: str) -> bool: ...

    @abstractmethod
    def size(self, path: str) -> int: ...

    @abstractmethod
    def files_under(self, top: str) -> list:
        """Every file below `top`, as paths relative to it."""

    @abstractmethod
    def glob(self, folder: str, pattern: str) -> list:
        """The paths in `folder` that match `pattern`, sorted."""

    @abstractmethod
    def find_executable(self, name: str, search_path: Optional[str] = None) -> Optional[str]: ...

    @abstractmethod
    def read_text(self, path: str) -> str:
        """The file's text; HeatmapError when it can't be read."""

    @abstractmethod
    def write_text(self, path: str, text: str) -> None:
        """Write the file, making its folders."""

    @abstractmethod
    def append_text(self, path: str, text: str) -> None: ...

    @abstractmethod
    def same_content(self, a: str, b: str) -> bool: ...

    @abstractmethod
    def make_dirs(self, path: str) -> None: ...

    @abstractmethod
    def copy_file(self, src: str, dst: str) -> None: ...

    @abstractmethod
    def copy_tree(self, src: str, dst: str) -> None:
        """Copy a folder, keeping symlinks as links."""

    @abstractmethod
    def remove(self, path: str) -> None:
        """Remove a file or a link."""

    @abstractmethod
    def remove_tree(self, path: str) -> None: ...


class KnowledgeGraph(ABC):
    """A tool that builds the knowledge graph of a working tree (code-review-graph): calls and execution flows."""

    @abstractmethod
    def graph_of(self, repo_root: str, data_dir: str) -> Graph:
        """The graph of the working tree at `repo_root`, built into `data_dir`."""


class Environment(ABC):
    """The process environment."""

    @abstractmethod
    def variables(self) -> dict:
        """A copy of every variable, as {name: value}."""

    @abstractmethod
    def get(self, name: str, default: Optional[str] = None) -> Optional[str]: ...

    @abstractmethod
    def paths(self, name: str) -> list:
        """The paths an os.pathsep-separated variable lists."""

    @abstractmethod
    def home(self) -> str: ...


class Clock(ABC):
    @abstractmethod
    def today(self) -> str:
        """Today, as YYYY-MM-DD."""


class DiagramPresenter(ABC):
    @abstractmethod
    def svg(self, heatmap: Heatmap) -> str:
        """The static treemap for a README."""

    @abstractmethod
    def readme_block(self, heatmap: Heatmap, image_link: str, fingerprint: str) -> str:
        """The README block, between the heat-map markers, that shows the treemap."""


class ReportPresenter(ABC):
    @abstractmethod
    def report(self, heatmap: Heatmap) -> str:
        """A short Markdown summary: the riskiest modules and what drives them."""
