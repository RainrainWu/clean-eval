"""Fixtures for the business rules' tests (clean_eval/use_cases/rules/): the domain models the rules read, beyond the
cross-layer ones of tests/conftest.py (`environ`, `policy`, `facts`, `source_files`, `ratings`, `heatmap`,
`judgement`, `review_rules`, `finding`, `agent_review`, `risk_report`), and the static assets the tests feed them.

The rules are pure functions over domain models and take no interfaces, so these are all data fixtures.
- A model fixture has a default, and reads its customisation from `request.param`; a test sets it with
  `@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test or on its whole class.
- A static asset (source files, a repository, a file's text, a README, secrets, a rules file) takes no param.
  A test takes it as an argument, or a decorator names it with pytest-lazy-fixtures' `lf`, mostly as the param of a
  model fixture: `@pytest.mark.parametrize("source_files", [lf("src_layout_files")], indirect=True)`."""

import re
from typing import Any

import pytest

from clean_eval.domain.evaluation import MergedPR
from clean_eval.domain.rating import AspectScores
from clean_eval.domain.review import Secrets
from clean_eval.use_cases.rules.heatmap import (
    MARKERS,
    SECTION_HEADING,
)

# The aspect scores of the module the `ratings` fixture rates by default.
ASPECT_SCORES: frozendict[str, Any] = frozendict({"biz": 40, "effects": 10, "entropy": 20, "unverified": 30})
MERGED_PR: frozendict[str, Any] = frozendict(
    {"sha": "a" * 40, "base": "b" * 40, "author": "a", "ts": 1, "subject": "s"}
)


# A rated module: its aspect scores, and aspect scores with one outstanding aspect.


@pytest.fixture
def aspect_scores(
    request,
):
    """A module's AspectScores: ASPECT_SCORES (those of the `rating` fixture's module: biz 40, effects 10, entropy 20,
    unverified 30) with the fields in `request.param` replaced; `{"unverified": None}` makes Unverified not
    applicable."""
    return AspectScores.model_validate({**ASPECT_SCORES, **getattr(request, "param", {})})


@pytest.fixture
def business_only_scores():
    """Aspect scores (an `aspect_scores` param) with one outstanding aspect, Business Impact at 95; the others at 0."""
    return {"biz": 95, "effects": 0, "entropy": 0, "unverified": 0}


@pytest.fixture
def unverified_only_scores():
    """Aspect scores (an `aspect_scores` param) with one outstanding aspect, Unverified at 100; the others at 0."""
    return {"biz": 0, "effects": 0, "entropy": 0, "unverified": 100}


# The code read from a repository: its source files (`source_files` specs), its knowledge graph and a file's text.


@pytest.fixture
def large_and_small_module_files():
    """A large module, a, with two sub-folders (400 lines each), and a small one, z (100 lines): `source_files`
    specs."""
    return [
        {"path": "a/b/x.py", "loc": 400},
        {"path": "a/c/y.py", "loc": 400},
        {"path": "z/w.py", "loc": 100},
    ]


@pytest.fixture
def src_layout_files():
    """A package in a src layout, whose two sub-packages sit at the end of a single-child chain (pkg/src/pkg), and a
    small module beside it: `source_files` specs."""
    return [
        {"path": "pkg/src/pkg/one/a.py", "loc": 300},
        {"path": "pkg/src/pkg/two/b.py", "loc": 300},
        {"path": "other/c.py", "loc": 50},
    ]


@pytest.fixture
def ext_and_dom_files():
    """src/, holding ext (three files, 300 lines) and dom (two files, 120 lines): `source_files` specs."""
    return [
        {"path": "src/ext/a.py", "loc": 100},
        {"path": "src/ext/b.py", "loc": 100},
        {"path": "src/ext/c.py", "loc": 100},
        {"path": "src/dom/d.py", "loc": 60},
        {"path": "src/dom/e.py", "loc": 60},
    ]


@pytest.fixture
def rated_module_names():
    """The names of rated modules: folders (src/api, src/core/deep, tools), a folder split per file (src/ext, by its
    a.py), a split folder with files of its own (src/core) and the root."""
    return {"src/api", "src/ext/a.py", "src/core/(files)", "src/core/deep", "(root)", "tools"}


@pytest.fixture
def three_files_graph():
    """A knowledge graph's fields (a `graph` param) over three files of three modules (`three_files_modules`): a.py
    imports b.py, c.py is test code, and tests/test_a.py is linked to a.py's function f."""
    return {
        "nodes": {
            "a.py::f": ("Function", "a.py", False),
            "b.py::g": ("Function", "b.py", False),
            "c.py::t": ("Function", "c.py", True),
        },
        "links": {"a.py": {"b.py"}},
        "tested": {"a.py::f": {"tests/test_a.py"}},
    }


@pytest.fixture
def three_files_modules():
    """The module each file of `three_files_graph` belongs to, {file: module}: A, B and C."""
    return {"a.py": "A", "b.py": "B", "c.py": "C"}


@pytest.fixture
def typed_and_loose_functions():
    """The text of a Python file: a typed function with an if and a boolean operator, and an untyped one that swallows
    a broad exception."""
    return """
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
"""


@pytest.fixture
def aws_writes():
    """The text of a Python file whose AWS client writes twice, first on line 3."""
    return "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"


@pytest.fixture
def todo_file():
    """A file with two TODO markers (TODOS is no marker), whose content has the id b1: a `source_files` spec."""
    return {"path": "a.py", "loc": 1, "text": "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", "blob": "b1"}


@pytest.fixture
def package_repository():
    """A repository's files, {path: text}: a package in a src layout, a lambda that imports its sibling utils (another
    utils lives elsewhere), a tool named like a stdlib module, and a test of the package."""
    return {
        "src/pkg/__init__.py": "from .core import run\n",
        "src/pkg/core.py": (
            "import json\nfrom . import util\n\ndef run():\n    return util.helper()\n\n"
            "class Svc:\n    def go(self):\n        pass\n"
        ),
        "src/pkg/util.py": "def helper():\n    return 1\n",
        "tools/json.py": "X = 1\n",
        "lambdas/x/handler.py": "import utils\n\ndef handler(e, c):\n    return utils.f()\n",
        "lambdas/x/utils.py": "def f():\n    pass\n",
        "other/deep/utils.py": "def f():\n    pass\n",
        "tests/test_core.py": (
            "from pkg import run\nfrom pkg.core import Svc\n\n"
            "def test_run():\n    assert run() and Svc().go() is None\n"
        ),
    }


@pytest.fixture
def package_repository_files(
    package_repository,
):
    """The files of `package_repository` as `source_files` specs: one line each, those under tests/ test code, and each
    content's id made from its path."""
    return [
        {"path": path, "loc": 1, "text": text, "is_test": path.startswith("tests/"), "blob": "blob-" + path}
        for path, text in package_repository.items()
    ]


# The heat map's block as `readme` writes it, and the READMEs it goes in.


@pytest.fixture
def readme_block():
    """The heat map's block as `readme` writes it: between its markers, the fingerprint 0123456789ab and a table,
    `new`."""
    return MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"


@pytest.fixture
def readme_without_the_section():
    """A README without the heat map's section: a title and an intro."""
    return "# Title\n\nIntro.\n"


@pytest.fixture
def readme_with_the_block():
    """A README whose heat map section holds the block between its markers (an old table), followed by a section,
    Next."""
    return "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"


@pytest.fixture
def readme_without_markers():
    """A README whose heat map section holds a stale table but no markers, followed by a section, Next."""
    return "# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n"


# What a PR review reads: the secrets it redacts, the modules it touches, and a rules file.


@pytest.fixture
def token_secrets(
    github_token,
):
    """The Secrets a review is cleaned of: the value of `github_token`."""
    return Secrets(
        values=(github_token,),
    )


@pytest.fixture
def nested_secrets():
    """Secrets of two values, one of which holds the other: s3cr3t-value-123 and s3cr3t-value-1234."""
    return Secrets(
        values=("s3cr3t-value-123", "s3cr3t-value-1234"),
    )


@pytest.fixture
def framed_secrets():
    """Secrets of two patterns, as an environment names the credentials it knows: a token's whole shape (tok_ and
    lowercase letters), and a key framed by `key=`, whose group is the key alone."""
    return Secrets(
        patterns=(re.compile(r"tok_[a-z]+"), re.compile(r"key=([a-z]+)")),
    )


@pytest.fixture
def api_and_util_ratings():
    """Two rated modules (`ratings` specs): src/api in the Critical band (overall 90) and src/util in the Low band
    (overall 5)."""
    return [
        {"name": "src/api", "loc": 500, "biz": 90, "unverified": 90},
        {"name": "src/util", "loc": 100, "biz": 5, "effects": 0, "entropy": 5, "unverified": 5},
    ]


@pytest.fixture
def legacy_review_rules():
    """The rules of the `review_rules` fixture as a pre-v2 review-rules.md wrote them: the Moderate band under its old
    name, Medium."""
    return """
| ID | Severity | Min tier | Rule |
|---|---|---|---|
| CA-01 | blocker | Low | dependency rule |
| Q-02 | major | Medium | failure paths |
| Q-04 | minor | Medium | clarity |
"""


# The merged-PR history the eval reads.


@pytest.fixture
def merged_prs(
    request,
):
    """MergedPRs, one per spec in `request.param` (dicts of MergedPR fields over MERGED_PR: commit aaaa... merged onto
    bbbb...; `modules` as ModuleRecord dicts); one PR that touched no module when not parametrized."""
    return [MergedPR.model_validate({**MERGED_PR, **spec}) for spec in getattr(request, "param", [{}])]


@pytest.fixture
def unrated_only_pr():
    """A merged PR (a `merged_prs` spec) that touched only a module no rated module covers, infra, in the band unrated
    modules had then, Moderate."""
    return {"modules": [{"module": "infra", "band": "Moderate", "rated": False}]}
