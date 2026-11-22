"""Classifying paths and modules (use_cases/rules/classify.py): tests, scales, tooling, exclusions."""
import unittest

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules import classify

P = Policy()


class Classify(unittest.TestCase):
    def test_tests_scales_manifests_and_names(self):
        self.assertTrue(classify.is_test("tests/unit/test_x.py", P) and classify.is_test("src/conftest.py", P))
        self.assertFalse(classify.is_test("src/testing_utils.py", P))
        self.assertEqual([classify.classify_scale("tests/%s/test_a.py" % d) for d in ("e2e", "integration", "unit")], ["e2e", "integration", "unit"])
        for name in ("terraform/lambda_src", ".github/scripts", "k8s/overlays", "src/testing"):
            self.assertIsNotNone(classify.not_applicable(name, P), name)
        self.assertIsNone(classify.not_applicable("src/api", P))
        self.assertTrue(classify.has_core_keyword("src/ingestion", P) and not classify.has_core_keyword("src/helpers", P))
        self.assertTrue(classify.looks_like_tooling("scripts/x", "scripts/x/a.py", P))
        self.assertTrue(classify.is_migration("db/alembic/versions/001.py", P))

    def test_ignored_and_excluded_paths(self):
        for path in ("README.md", "pkg/README.rst", "CHANGELOG.md", "LICENSE", "docs/guide/x.py", "img/a.png"):
            self.assertTrue(classify.ignored(path, P), path)
        # dependency changes, agent instructions and review rules steer behaviour: they carry risk
        for path in ("requirements.txt", "SKILL.md", ".claude/skills/x/SKILL.md", "CLAUDE.md", "AGENTS.md",
                     ".clean-eval/review-rules.md"):
            self.assertFalse(classify.ignored(path, P), path)
        p = Policy.from_overrides({"exclude": ["vendor/*"]})
        self.assertTrue(classify.excluded("vendor/lib/x.py", p) and not classify.excluded("src/x.py", p))


if __name__ == "__main__":
    unittest.main()
