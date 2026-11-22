"""A change's risk (use_cases/rules/change.py): every changed path takes its module's band, or the unrated band."""

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules.change import change_risk
from clean_eval.use_cases.rules.scoring import band
from tests.domain.builders import heatmap, rating

P = Policy()


def test_paths_take_their_module_band_and_unknown_paths_the_unrated_band():
    h = heatmap(
        rating("src/api", 500, biz=90, unverified=90),
        rating("src/util", 100, biz=5, effects=0, entropy=5, unverified=5),
    )
    risk = change_risk(h, ["src/api/x.py", "src/util/y.py", "README.md", "terraform/main.tf", "src/new/z.py"], P)
    assert risk.files["src/api/x.py"] == "src/api"
    assert risk.ignored == ["README.md"]
    mods = {m.module: m for m in risk.modules}
    assert mods["src/util"].band == "Low"
    assert not mods["terraform"].rated
    assert (mods["terraform"].band, mods["src/new"].band) == ("Moderate", "Moderate")
    assert risk.max_band == band(mods["src/api"].score)
    assert risk.modules[0].module == "src/api"  # most severe first


def test_agent_instructions_are_not_risk_free():
    risk = change_risk(heatmap(), [".claude/skills/pr-review/SKILL.md", ".clean-eval/review-rules.md"], P)
    assert (risk.max_band, risk.ignored) == ("Moderate", [])


def test_docs_only_and_empty_changes_are_low():
    assert change_risk(heatmap(), ["docs/a.md"], P).max_band == "Low"
    assert change_risk(heatmap(), [], P).max_score == 0


def test_unrated_only_change_scores_the_band_floor():
    risk = change_risk(heatmap(), ["infra/main.tf"], Policy.from_overrides({"unrated_band": "High"}))
    assert (risk.max_band, risk.max_score) == ("High", 50)
