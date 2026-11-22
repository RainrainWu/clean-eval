"""A change's risk (use_cases/rules/change.py): every changed path takes its module's band, or the unrated band."""
import unittest

from tests.domain.builders import heatmap, rating

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules.change import change_risk
from clean_eval.use_cases.rules.scoring import band

P = Policy()


class ChangeRiskTest(unittest.TestCase):
    def test_paths_take_their_module_band_and_unknown_paths_the_unrated_band(self):
        h = heatmap(rating("src/api", 500, biz=90, unverified=90), rating("src/util", 100, biz=5, effects=0, entropy=5, unverified=5))
        risk = change_risk(h, ["src/api/x.py", "src/util/y.py", "README.md", "terraform/main.tf", "src/new/z.py"], P)
        self.assertEqual(risk.files["src/api/x.py"], "src/api")
        self.assertEqual(risk.ignored, ["README.md"])
        mods = {m.module: m for m in risk.modules}
        self.assertEqual(mods["src/util"].band, "Low")
        self.assertFalse(mods["terraform"].rated)
        self.assertEqual((mods["terraform"].band, mods["src/new"].band), ("Moderate", "Moderate"))
        self.assertEqual(risk.max_band, band(mods["src/api"].score))
        self.assertEqual(risk.modules[0].module, "src/api")  # most severe first

    def test_agent_instructions_are_not_risk_free(self):
        risk = change_risk(heatmap(), [".claude/skills/pr-review/SKILL.md", ".clean-eval/review-rules.md"], P)
        self.assertEqual((risk.max_band, risk.ignored), ("Moderate", []))

    def test_docs_only_and_empty_changes_are_low(self):
        self.assertEqual(change_risk(heatmap(), ["docs/a.md"], P).max_band, "Low")
        self.assertEqual(change_risk(heatmap(), [], P).max_score, 0)

    def test_unrated_only_change_scores_the_band_floor(self):
        risk = change_risk(heatmap(), ["infra/main.tf"], Policy.from_overrides({"unrated_band": "High"}))
        self.assertEqual((risk.max_band, risk.max_score), ("High", 50))


if __name__ == "__main__":
    unittest.main()
