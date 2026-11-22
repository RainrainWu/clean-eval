"""The four risk aspects (use_cases/rules/aspects.py): Business Impact, Side Effects, Entropy and Unverified."""

from collections import Counter

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules import aspects
from tests.domain.builders import facts

P = Policy()


def test_tooling_scales_business_score():
    plain, _ = aspects.business(facts(fan_in=6, keyword=True), P)
    tool, ev = aspects.business(facts(fan_in=6, keyword=True, tooling=True), P)
    assert tool == pytest.approx(plain * 0.5)
    assert ev[0].startswith("Tooling path")


def test_business_without_flow_data_rescales_the_other_terms():
    with_flows, _ = aspects.business(facts(fan_in=6, keyword=True, share=0.25), P)
    no_flows, ev = aspects.business(facts(fan_in=6, keyword=True, share=0.25, flows_available=False), P)
    assert with_flows == pytest.approx(55.0)  # dependents, keyword and size at full strength; no flows
    assert no_flows == pytest.approx(100.0)  # the same terms carry the whole weight
    assert any("no execution-flow data" in e for e in ev)


def test_entropy_without_functions_uses_neutral_terms():
    score, ev = aspects.entropy(facts(funcs=[], typed_share=None), P)
    assert any("No functions to measure" in e for e in ev)
    assert 0 < score < 40


def test_effects_grow_with_writes_and_migrations():
    quiet, _ = aspects.effects(facts(), P)
    busy, ev = aspects.effects(
        facts(effect_counts=Counter(db_write=10), effect_examples={"db_write": "a.py:1"}, migrations=3, fan_in=4), P
    )
    assert busy > quiet + 40
    assert any("migration" in e for e in ev)


def test_unverified_rules():
    neutral, _, info = aspects.unverified(
        facts(graph_functions=0, linked_functions=0, linked_test_loc=0.0, test_scales=Counter()), 0, P
    )
    assert (neutral, info["coverage"]) == (P.unverified_no_functions, None)
    base, _, _ = aspects.unverified(facts(test_scales=Counter(unit=3)), 10, P)
    risky, ev, _ = aspects.unverified(facts(test_scales=Counter(unit=3)), 80, P)
    assert risky == base + P.no_integration_penalty
    untested, _, _ = aspects.unverified(
        facts(linked_functions=0, linked_test_files=0, linked_test_loc=0.0, test_scales=Counter()), 0, P
    )
    assert untested >= 99
    na, ev, _ = aspects.unverified(facts(na_reason="Infrastructure manifest"), 90, P)
    assert na is None
    assert ev[0].startswith("Not applicable")
