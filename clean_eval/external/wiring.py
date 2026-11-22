"""The composition root: the implementations the use-case functions are given. One gateway per external dependency
(the use cases' interfaces Git, CodeHost, FileSystem, KnowledgeGraph, Environment, Clock) and the presenters. The
command line passes them to the use cases."""
from pydantic import ValidationError

from clean_eval.adaptor.presenters.diagram import SvgDiagramPresenter
from clean_eval.adaptor.presenters.markdown_report import MarkdownReportPresenter
from clean_eval.domain.errors import InvalidRequest
from clean_eval.domain.history import Period
from clean_eval.external.clock import ClockGateway
from clean_eval.external.code_review_graph import CodeReviewGraphGateway
from clean_eval.external.environment import EnvironmentGateway
from clean_eval.external.filesystem import FileSystemGateway
from clean_eval.external.git import GitGateway
from clean_eval.external.github import GitHubGateway
from clean_eval.use_cases.rules.redaction import secret_values

GRAPHS = ("builtin", "code-review-graph")
# The stateless gateways, shared; git is per repository, gh per use.
FS, ENV, CLOCK = FileSystemGateway(), EnvironmentGateway(), ClockGateway()
CHECKOUT = FS.parent(FS.parent(FS.parent(FS.real_path(__file__))))  # the clean-eval checkout: package and skills
DEFAULT_RULES = FS.join(CHECKOUT, ".claude", "skills", "pr-review", "review-rules.md")


def git(repo):
    return GitGateway(repo)


def github():
    return GitHubGateway()


def knowledge_graph():
    """The code-review-graph CLI: on PATH, else where its installer puts it."""
    return CodeReviewGraphGateway(FS.find_executable("code-review-graph", ENV.get("PATH"))
                                  or FS.join(ENV.home(), ".local", "bin", "code-review-graph"))


def diagram_presenter():
    return SvgDiagramPresenter()


def report_presenter():
    return MarkdownReportPresenter()


def today():
    return CLOCK.today()


def secrets():
    """The values to redact: the secret-looking variables of this process's environment."""
    return secret_values(ENV.variables())


def default_rules():
    return FS.read_text(DEFAULT_RULES)


def repository_root(path):
    return GitGateway(path).toplevel()


def period(since, until, window_days=30):
    """The Period of PRs merged from `since` to `until` (YYYY-MM-DD, inclusive), with `window_days` of look-ahead."""
    try:
        return Period(since=since, until=until, window_days=window_days)
    except ValidationError as e:
        raise InvalidRequest.from_validation(e, "since/until must be dates as YYYY-MM-DD")
