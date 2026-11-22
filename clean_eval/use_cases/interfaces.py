"""The abstract classes of every external utility the business logic uses, in its own terms. The use-case functions
take them as parameters and call only their abstract methods, so they never know (or import) what is behind them; the
outer layers inherit and implement them:

  Git, CodeHost, FileSystem, CoverageReport,      the external layer's gateways (external/*.py)
  Agent, Environment, Clock, Runner
  DiagramPresenter, ReportPresenter               the adaptor layer's presenters (rendering only)
  RunLog                                          the command line's console (external/cli.py)
"""

from abc import (
    ABC,
    abstractmethod,
)
from typing import Optional

from clean_eval.domain.rating import Heatmap
from clean_eval.domain.retro import ReviewThreads
from clean_eval.domain.review import (
    AgentRun,
    AgentTask,
    FileDiff,
    HostReview,
    PullRequest,
    ReviewPost,
    ReviewPosting,
    RunFolders,
    Secrets,
    StepOutputs,
)


class Git(ABC):
    """A git repository: the sources of its working tree and revisions, its history, diffs and patches, and the ids it
    gives files' contents."""

    @abstractmethod
    def toplevel(
        self,
    ) -> str:
        """The repository's root folder."""

    @abstractmethod
    def is_shallow(
        self,
    ) -> bool:
        """Whether the repository is a shallow clone, missing the history before its oldest commit."""

    @abstractmethod
    def rev_parse(
        self,
        rev: str,
        short: bool = False,
    ) -> str:
        """The commit `rev` names (short: abbreviated)."""

    @abstractmethod
    def has_commit(
        self,
        rev: str,
    ) -> bool:
        """Whether the commit is here."""

    @abstractmethod
    def merge_base(
        self,
        first_rev: str,
        second_rev: str,
    ) -> str:
        """The best common ancestor of two revisions."""

    @abstractmethod
    def branch(
        self,
    ) -> str:
        """The checked-out branch ("" when detached)."""

    @abstractmethod
    def repo_name(
        self,
    ) -> str:
        """The repository's name (from its origin, else its folder)."""

    @abstractmethod
    def ls_files(
        self,
    ) -> list[str]:
        """The tracked paths of the working tree."""

    @abstractmethod
    def ls_tree(
        self,
        rev: str,
    ) -> list[tuple[str, str, int]]:
        """[(path, blob id, size)] of the files at `rev`."""

    @abstractmethod
    def read_blobs(
        self,
        blob_ids: list[str],
    ) -> dict[str, str]:
        """{blob id: text} of the given blobs."""

    @abstractmethod
    def diff_files(
        self,
        base: str,
        head: str,
    ) -> list[str]:
        """The paths changed between two revisions."""

    @abstractmethod
    def show(
        self,
        rev: str,
        path: str,
    ) -> Optional[str]:
        """The file `path` at `rev`, or None when there is none."""

    @abstractmethod
    def ensure_commit(
        self,
        sha: str,
        refspec: str,
    ) -> None:
        """Fetch `refspec` when `sha` is missing; HeatmapError when it still is."""

    @abstractmethod
    def file_diffs(
        self,
        base: str,
        head: str,
        paths: list[str],
    ) -> list[FileDiff]:
        """The diff base..head of each of `paths`, in their order (renames detected): its patch, the lines it adds and
        removes, and the lines of the new file its hunks show."""

    @abstractmethod
    def blob_id(
        self,
        text: str,
    ) -> str:
        """The id git gives `text` as a file's content (what `git hash-object` prints for a file holding it)."""

    @abstractmethod
    def file_log(
        self,
        rev: str,
        path: str,
        limit: int = 10,
    ) -> str:
        """One line per commit that touched `path`, up to `rev`, newest first."""

    @abstractmethod
    def commit_patch(
        self,
        sha: str,
        paths: list[str],
    ) -> str:
        """A commit's message, stat and patch, limited to `paths`."""

    @abstractmethod
    def default_branch(
        self,
    ) -> str:
        """The branch PRs are merged into."""

    @abstractmethod
    def first_parent_log(
        self,
        branch: str,
        since: str,
        until: str,
    ) -> list[tuple[str, list[str], str, int, str]]:
        """[(sha, [parent shas], author, unix time, subject)] of the first-parent commits of `branch` in the period."""


class CodeHost(ABC):
    """The pull requests of the repository on its code host, and how the host names the commits that merge them.
    Failures raise RemoteError."""

    @abstractmethod
    def pull_request(
        self,
        number: int,
    ) -> PullRequest:
        """A PR: its title, body, author, URL, base and head refs and commits, and where its head is fetched from."""

    @abstractmethod
    def require_auth(
        self,
    ) -> None:
        """HeatmapError unless the host can be called."""

    @abstractmethod
    def review_threads(
        self,
        number: int,
    ) -> ReviewThreads:
        """What was said on a PR: its reviews, comments and inline review comments, with its URL, labels and size."""

    @abstractmethod
    def post_review(
        self,
        number: int,
        post: ReviewPost,
    ) -> ReviewPosting:
        """Post `post` as one review of PR `number`, as much of it as the host takes: when the host refuses its inline
        comments or its request for changes, again without them. Each attempt, and the review as posted (None when the
        host refused them all)."""

    @abstractmethod
    def reviews(
        self,
        number: int,
    ) -> list[HostReview]:
        """Every review of the PR."""

    @abstractmethod
    def dismiss_review(
        self,
        number: int,
        review_id: int,
        message: str,
    ) -> None:
        """Dismiss one review."""

    @abstractmethod
    def merged_pr(
        self,
        subject: str,
    ) -> Optional[int]:
        """The PR a commit of the main branch merged, from its subject, as the host writes a merge's or a squash's; None
        for a direct push."""


class FileSystem(ABC):
    """Files and folders, and the path strings that name them."""

    sep: str  # the path separator

    @abstractmethod
    def join(
        self,
        *parts: str,
    ) -> str: ...

    @abstractmethod
    def parent(
        self,
        path: str,
    ) -> str: ...

    @abstractmethod
    def name(
        self,
        path: str,
    ) -> str: ...

    @abstractmethod
    def relative(
        self,
        path: str,
        start: str,
    ) -> str: ...

    @abstractmethod
    def absolute(
        self,
        path: str,
    ) -> str: ...

    @abstractmethod
    def real_path(
        self,
        path: str,
    ) -> str:
        """The path with every symlink resolved."""

    @abstractmethod
    def expand_user(
        self,
        path: str,
    ) -> str: ...

    @abstractmethod
    def cwd(
        self,
    ) -> str: ...

    @abstractmethod
    def exists(
        self,
        path: str,
    ) -> bool: ...

    @abstractmethod
    def lexists(
        self,
        path: str,
    ) -> bool:
        """Whether anything is at `path`, a dangling symlink included."""

    @abstractmethod
    def is_file(
        self,
        path: str,
    ) -> bool: ...

    @abstractmethod
    def is_dir(
        self,
        path: str,
    ) -> bool: ...

    @abstractmethod
    def is_link(
        self,
        path: str,
    ) -> bool: ...

    @abstractmethod
    def size(
        self,
        path: str,
    ) -> int: ...

    @abstractmethod
    def files_under(
        self,
        top: str,
    ) -> list[str]:
        """Every file below `top`, as paths relative to it."""

    @abstractmethod
    def glob(
        self,
        folder: str,
        pattern: str,
    ) -> list[str]:
        """The paths in `folder` that match `pattern`, sorted."""

    @abstractmethod
    def find_executable(
        self,
        name: str,
        search_path: Optional[str] = None,
    ) -> Optional[str]: ...

    @abstractmethod
    def read_text(
        self,
        path: str,
    ) -> str:
        """The file's text; HeatmapError when it can't be read."""

    @abstractmethod
    def write_text(
        self,
        path: str,
        text: str,
    ) -> None:
        """Write the file, making its folders."""

    @abstractmethod
    def append_text(
        self,
        path: str,
        text: str,
    ) -> None: ...

    @abstractmethod
    def same_content(
        self,
        first_path: str,
        second_path: str,
    ) -> bool: ...

    @abstractmethod
    def make_dirs(
        self,
        path: str,
    ) -> None: ...

    @abstractmethod
    def copy_file(
        self,
        src: str,
        dst: str,
    ) -> None: ...

    @abstractmethod
    def copy_tree(
        self,
        src: str,
        dst: str,
    ) -> None:
        """Copy a folder, keeping symlinks as links."""

    @abstractmethod
    def remove(
        self,
        path: str,
    ) -> None:
        """Remove a file or a link."""

    @abstractmethod
    def remove_tree(
        self,
        path: str,
    ) -> None: ...


class CoverageReport(ABC):
    """The line coverage a working tree's tests measured, from the coverage report they left in it."""

    @abstractmethod
    def line_rates(
        self,
        root: str,
    ) -> dict[str, float]:
        """{file path, relative to `root`: the share of its lines covered, 0-1}, from the coverage report at or one
        level below the working tree `root`; {} when there is none."""


class Agent(ABC):
    """The coding agent a CI run hands its task to. It runs sandboxed: no shell, no network and no token; it reads the
    workspace and the context folder, writes only to the folders it is given, and follows its own skills, never
    instructions from the checkout."""

    @abstractmethod
    def require(
        self,
    ) -> None:
        """Make sure the agent can run (installing it when it is missing); HeatmapError when it can't, such as without
        credentials in CI."""

    @abstractmethod
    def run(
        self,
        task: AgentTask,
        folders: RunFolders,
        writable: list[str],
    ) -> AgentRun:
        """Run the agent on `task`, following its skill, in `folders.workspace`: it may read the workspace and
        `folders.context`, and write only under `folders.agent` and the folders in `writable`. Its exit status and its
        transcript."""


class Environment(ABC):
    """The process environment, as a whole: every variable (what the agent may be passed), the secrets it holds, and
    path lists (the guard's roots). A run's settings, each read from its own variable, are Settings
    (clean_eval/settings.py)."""

    @abstractmethod
    def variables(
        self,
    ) -> dict[str, str]:
        """A copy of every variable, as {name: value}."""

    @abstractmethod
    def secrets(
        self,
    ) -> Secrets:
        """The secrets to clean from text that leaves the runner: the values of the secret-looking variables, in every
        form the runner uses them, and the patterns of well-known credentials."""

    @abstractmethod
    def paths(
        self,
        name: str,
    ) -> list[str]:
        """The paths an os.pathsep-separated variable lists."""

    @abstractmethod
    def home(
        self,
    ) -> str: ...


class Clock(ABC):
    @abstractmethod
    def today(
        self,
    ) -> str:
        """Today, as YYYY-MM-DD."""


class Runner(ABC):
    """The CI runner a run is on, which keeps what the run reports after it ends: the outputs of its step, which later
    steps read, and the job summary, which people read."""

    @abstractmethod
    def set_outputs(
        self,
        outputs: StepOutputs,
    ) -> None:
        """Set these outputs of the step, beside those it already has."""

    @abstractmethod
    def add_summary(
        self,
        title: str,
        report: str,
    ) -> None:
        """Add `report` (Markdown) to the job summary, under `title`."""


class DiagramPresenter(ABC):
    @abstractmethod
    def svg(
        self,
        heatmap: Heatmap,
    ) -> str:
        """The static treemap for a README."""

    @abstractmethod
    def readme_block(
        self,
        heatmap: Heatmap,
        image_link: str,
        fingerprint: str,
    ) -> str:
        """The README block, between the heat-map markers, that shows the treemap."""


class ReportPresenter(ABC):
    @abstractmethod
    def report(
        self,
        heatmap: Heatmap,
    ) -> str:
        """A short Markdown summary: the riskiest modules and what drives them."""


class RunLog(ABC):
    """Where a CI run reports its progress: the runner's log, which people read while it runs."""

    @abstractmethod
    def step(
        self,
        title: str,
    ) -> None:
        """Start a step of the run: what it does next."""

    @abstractmethod
    def warning(
        self,
        message: str,
    ) -> None:
        """Report something that went wrong without failing the run."""

    @abstractmethod
    def show(
        self,
        text: str,
    ) -> None:
        """Show a block of text in the current step, such as the agent's transcript."""
