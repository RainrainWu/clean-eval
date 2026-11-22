"""Rating one module (use_cases/rules/rating.py) from its facts, and rescoring it under another policy."""

from collections import Counter

from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Facts
from clean_eval.use_cases.rules.rating import rate_module, rescore
from tests.domain.builders import facts

P = Policy()


def test_stored_facts_reproduce_the_rating():
    f = facts(effect_counts=Counter(db_write=2), test_scales=Counter(unit=1), funcs=[(70, 18), (5, 1)])
    r = rate_module(f, [], P)
    stored = Facts.model_validate_json(r.facts.model_dump_json())  # as data.json or a history keeps them
    assert stored == f
    assert rescore(stored, P) == r.scores
    assert r.baseline.model_dump() == r.scores.model_dump(exclude={"overall"})
    louder = Policy.from_overrides({"effect_points": {"db_write": 30}})
    assert rescore(stored, louder).effects > r.scores.effects


def test_scores_stay_in_range_when_tuned_weights_sum_past_one():
    heavy = Policy.from_overrides({"business_weights": {"dependents": 0.9, "keyword": 0.9, "size": 0.9}})
    r = rate_module(facts(fan_in=12, keyword=True, share=1.0), [], heavy)
    assert r.scores.biz == 100
