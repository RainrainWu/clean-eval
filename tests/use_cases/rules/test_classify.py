"""Classifying paths and modules (use_cases/rules/classify.py): tests, scales, tooling, exclusions."""

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules import classify

P = Policy()


def test_tests_scales_manifests_and_names():
    assert classify.is_test("tests/unit/test_x.py", P)
    assert classify.is_test("src/conftest.py", P)
    assert not classify.is_test("src/testing_utils.py", P)
    assert [classify.classify_scale("tests/%s/test_a.py" % d) for d in ("e2e", "integration", "unit")] == [
        "e2e",
        "integration",
        "unit",
    ]
    for name in ("terraform/lambda_src", ".github/scripts", "k8s/overlays", "src/testing"):
        assert classify.not_applicable(name, P) is not None, name
    assert classify.not_applicable("src/api", P) is None
    assert classify.has_core_keyword("src/ingestion", P)
    assert not classify.has_core_keyword("src/helpers", P)
    assert classify.looks_like_tooling("scripts/x", "scripts/x/a.py", P)
    assert classify.is_migration("db/alembic/versions/001.py", P)


@pytest.mark.parametrize(
    "path", ["README.md", "pkg/README.rst", "CHANGELOG.md", "LICENSE", "docs/guide/x.py", "img/a.png"]
)
def test_docs_and_assets_are_ignored(path):
    assert classify.ignored(path, P)


@pytest.mark.parametrize(
    "path",
    [
        "requirements.txt",
        "SKILL.md",
        ".claude/skills/x/SKILL.md",
        "CLAUDE.md",
        "AGENTS.md",
        ".clean-eval/review-rules.md",
    ],
)
def test_dependencies_agent_instructions_and_review_rules_carry_risk(path):
    assert not classify.ignored(path, P)  # they steer behaviour


def test_excluded_paths():
    p = Policy.from_overrides({"exclude": ["vendor/*"]})
    assert classify.excluded("vendor/lib/x.py", p)
    assert not classify.excluded("src/x.py", p)
