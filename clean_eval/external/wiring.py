"""The composition root: build the gateways (one per external dependency), the adaptors over them, and the use cases
from both. The command line gets everything it runs from here."""
from pydantic import ValidationError

from ..adaptor.ast_graph import BuiltinGraph
from ..adaptor.code_review_graph import CodeReviewGraph
from ..adaptor.coverage_xml import CoverageXmlReader, NoCoverage
from ..adaptor.files import FileReadmeStore, Folder, Workspace, append_step_outputs as _append_step_outputs
from ..adaptor.files import load_policy as _load_policy, read_json as _read_json
from ..adaptor.git_source import RevisionSource, WorkingTreeSource
from ..adaptor.python_signals import PythonSignals
from ..adaptor.tuning_files import TuningFiles
from ..domain.errors import InvalidRequest
from ..domain.history import Period
from ..use_cases.assess_change import AssessChange
from ..use_cases.collect_history import CollectHistory
from ..use_cases.finalize_run import FinalizeRun
from ..use_cases.post_review import PostReview
from ..use_cases.prepare_eval import PrepareEval
from ..use_cases.prepare_review import PrepareReview
from ..use_cases.rate_repository import RateRepository
from ..use_cases.rules.redaction import Redactor, secret_values
from .clock import ClockGateway
from .code_review_graph import CodeReviewGraphGateway
from .environment import EnvironmentGateway
from .filesystem import FileSystemGateway
from .git import GitGateway
from .github import GitHubGateway
from .sqlite import SqliteGateway

GRAPHS = ("builtin", "code-review-graph")
SNAPSHOT = "clean-eval.orig"  # in the context folder: .clean-eval/ as it was before the agent ran
# The stateless gateways, shared; git is per repository, gh per use.
FS, ENV, CLOCK = FileSystemGateway(), EnvironmentGateway(), ClockGateway()
CHECKOUT = FS.parent(FS.parent(FS.parent(FS.real_path(__file__))))  # the clean-eval checkout: package and skills
DEFAULT_RULES = FS.join(CHECKOUT, ".claude", "skills", "pr-review", "review-rules.md")


def today():
    return CLOCK.today()


def period(since, until, window_days=30):
    """The Period of PRs merged from `since` to `until` (YYYY-MM-DD, inclusive), with `window_days` of look-ahead."""
    try:
        return Period(since=since, until=until, window_days=window_days)
    except ValidationError as e:
        raise InvalidRequest.from_validation(e, "since/until must be dates as YYYY-MM-DD")


def redactor(environ=None):
    """Redacts the values of the secret-looking variables in the environment (this process's by default)."""
    return Redactor(secret_values(ENV.variables() if environ is None else environ))


def repository_root(path):
    return GitGateway(path).toplevel()


def load_policy(repo_root=None, path=None):
    return _load_policy(FS, repo_root, path)


def read_json(path):
    return _read_json(FS, path)


def append_step_outputs(path, outputs):
    _append_step_outputs(FS, path, outputs)


def workspace():
    return Workspace(FS)


def readme_store(readme_path, svg_path):
    return FileReadmeStore(FS, readme_path, svg_path)


def folder(root):
    return Folder(FS, root)


def code_review_graph():
    """The code-review-graph CLI: on PATH, else where its installer puts it."""
    return CodeReviewGraphGateway(FS.find_executable("code-review-graph", ENV.get("PATH"))
                                  or FS.join(ENV.home(), ".local", "bin", "code-review-graph"))


def working_tree_rater(repo, policy, graph="builtin", graph_dir=None):
    """Rate the working tree as it is on disk."""
    git = GitGateway(repo)
    if graph == "code-review-graph":
        data_dir = graph_dir or FS.join(git.toplevel(), ".git", "clean-eval-graph")
        provider = CodeReviewGraph(FS, code_review_graph(), SqliteGateway, data_dir)
    elif graph == "builtin":
        provider = BuiltinGraph()
    else:
        raise InvalidRequest("--graph must be one of %s" % ", ".join(GRAPHS))
    return RateRepository(WorkingTreeSource(git, FS, policy), provider, PythonSignals(), CoverageXmlReader(FS))


class RevisionRaters:
    """Raters for many revisions of one repository, sharing file contents and per-file analysis between them. The
    RevisionRisk port of the use cases."""

    def __init__(self, repo):
        self.git = GitGateway(repo)
        self.blobs, self.graph, self.signals = {}, BuiltinGraph(), PythonSignals()

    def source(self, rev, policy):
        return RevisionSource(self.git, rev, policy, self.blobs)

    def rater(self, source):
        return RateRepository(source, self.graph, self.signals, NoCoverage())

    def at(self, rev, policy):
        return self.rater(self.source(rev, policy))

    def heatmap_at(self, rev, policy):
        return self.at(rev, policy).execute(policy, today())

    def assess(self, base, head, policy):
        """(heatmap at base, ChangeRisk of base..head): what the PR review scales its depth by."""
        source = self.source(base, policy)
        return AssessChange(self.rater(source), source).execute(self.git.diff_files(base, head), policy, today())


def assess_change(repo, base, head, policy):
    return RevisionRaters(repo).assess(base, head, policy)


def default_rules():
    return FS.read_text(DEFAULT_RULES)


def tuning_files(workspace_root, ctx):
    return TuningFiles(FS, workspace_root, FS.join(ctx, SNAPSHOT))


def collect_history(repo, with_gh=False):
    return CollectHistory(GitGateway(repo), RevisionRaters(repo), GitHubGateway() if with_gh else None)


def prepare_review(repo, ctx):
    return PrepareReview(GitHubGateway(), GitGateway(repo), RevisionRaters(repo), folder(ctx), default_rules())


def prepare_eval(repo, ctx, with_gh=False):
    return PrepareEval(collect_history(repo, with_gh), GitGateway(repo), tuning_files(repo, ctx), folder(ctx),
                       default_rules())


def finalize_run(ctx, agent, out, workspace_root="."):
    return FinalizeRun(folder(ctx), folder(agent), folder(out), redactor(), tuning_files(workspace_root, ctx))


def post_review(out, ctx=None):
    return PostReview(GitHubGateway(), folder(out), folder(ctx) if ctx else None, redactor())
