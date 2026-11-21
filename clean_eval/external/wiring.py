"""The composition root: build use cases from concrete adaptors. The CLI and the CI scripts get their use cases here."""
import datetime as dt
import os

from ..adaptor.gateways.ast_graph import BuiltinGraph
from ..adaptor.gateways.code_review_graph import CodeReviewGraph
from ..adaptor.gateways.coverage_xml import CoverageXmlReader, NoCoverage
from ..adaptor.gateways.files import load_policy  # noqa: F401  (re-exported for callers of the wiring)
from ..adaptor.gateways.git_cli import Git
from ..adaptor.gateways.git_source import RevisionSource, WorkingTreeSource
from ..adaptor.gateways.python_signals import PythonSignals
from ..domain.errors import InvalidRequest
from ..use_cases.assess_change import AssessChange
from ..use_cases.rate_repository import RateRepository

GRAPHS = ("builtin", "code-review-graph")
TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "template.html")


def today():
    return dt.date.today().isoformat()


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
    """Raters for many revisions of one repository, sharing file contents and per-file analysis between them."""

    def __init__(self, repo):
        self.git = Git(repo)
        self.blobs, self.graph, self.signals = {}, BuiltinGraph(), PythonSignals()

    def source(self, rev, policy):
        return RevisionSource(self.git, rev, policy, self.blobs)

    def rater(self, source):
        return RateRepository(source, self.graph, self.signals, NoCoverage())

    def at(self, rev, policy):
        return self.rater(self.source(rev, policy))


def assess_change(repo, base, head, policy):
    """(heatmap at base, ChangeRisk of base..head): what the PR review scales its depth by."""
    raters = RevisionRaters(repo)
    source = raters.source(base, policy)
    return AssessChange(raters.rater(source), source).execute(raters.git.diff_files(base, head), policy, today())
