"""The composition root: build use cases from concrete adaptors. The command line gets its use cases here."""
import datetime as dt
import os
from pathlib import Path

from pydantic import ValidationError

from ..adaptor.gateways.ast_graph import BuiltinGraph
from ..adaptor.gateways.code_review_graph import CodeReviewGraph
from ..adaptor.gateways.coverage_xml import CoverageXmlReader, NoCoverage
from ..adaptor.gateways.files import Folder, read_text
from ..adaptor.gateways.git_cli import Git
from ..adaptor.gateways.git_source import RevisionSource, WorkingTreeSource
from ..adaptor.gateways.github_cli import GitHubCli
from ..adaptor.gateways.python_signals import PythonSignals
from ..adaptor.gateways.tuning_files import TuningFiles
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

GRAPHS = ("builtin", "code-review-graph")
CHECKOUT = Path(__file__).resolve().parents[2]  # the clean-eval checkout that holds this package and the skills
DEFAULT_RULES = CHECKOUT / ".claude" / "skills" / "pr-review" / "review-rules.md"
SNAPSHOT = "clean-eval.orig"  # in the context folder: .clean-eval/ as it was before the agent ran


def today():
    return dt.date.today().isoformat()


def period(since, until, window_days=30):
    """The Period of PRs merged from `since` to `until` (YYYY-MM-DD, inclusive), with `window_days` of look-ahead."""
    try:
        return Period(since=since, until=until, window_days=window_days)
    except ValidationError as e:
        raise InvalidRequest.from_validation(e, "since/until must be dates as YYYY-MM-DD")


def redactor(environ=None):
    """Redacts the values of the secret-looking variables in the environment (this process's by default)."""
    return Redactor(secret_values(os.environ if environ is None else environ))


def working_tree_rater(repo, policy, graph="builtin", graph_dir=None):
    """Rate the working tree as it is on disk."""
    git = Git(repo)
    if graph == "code-review-graph":
        provider = CodeReviewGraph(graph_dir or os.path.join(git.toplevel(), ".git", "clean-eval-graph"))
    elif graph == "builtin":
        provider = BuiltinGraph()
    else:
        raise InvalidRequest("--graph must be one of %s" % ", ".join(GRAPHS))
    return RateRepository(WorkingTreeSource(git, policy), provider, PythonSignals(), CoverageXmlReader())


class RevisionRaters:
    """Raters for many revisions of one repository, sharing file contents and per-file analysis between them. The
    RevisionRisk port of the use cases."""

    def __init__(self, repo):
        self.git = Git(repo)
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
    return read_text(DEFAULT_RULES)


def collect_history(repo, with_gh=False):
    return CollectHistory(Git(repo), RevisionRaters(repo), GitHubCli() if with_gh else None)


def prepare_review(repo, ctx):
    return PrepareReview(GitHubCli(), Git(repo), RevisionRaters(repo), Folder(ctx), default_rules())


def prepare_eval(repo, ctx, with_gh=False):
    return PrepareEval(collect_history(repo, with_gh), Git(repo), TuningFiles(repo, os.path.join(ctx, SNAPSHOT)), Folder(ctx),
                       default_rules())


def finalize_run(ctx, agent, out, workspace="."):
    return FinalizeRun(Folder(ctx), Folder(agent), Folder(out), redactor(), TuningFiles(workspace, os.path.join(ctx, SNAPSHOT)))


def post_review(out, ctx=None):
    return PostReview(GitHubCli(), Folder(out), Folder(ctx) if ctx else None, redactor())
