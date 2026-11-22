"""The four risk aspects (use_cases/rules/aspects.py): Business Impact, Side Effects, Entropy and Unverified."""
import unittest
from collections import Counter

from tests.domain.builders import facts

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules import aspects

P = Policy()


class Aspects(unittest.TestCase):
    def test_tooling_scales_business_score(self):
        plain, _ = aspects.business(facts(fan_in=6, keyword=True), P)
        tool, ev = aspects.business(facts(fan_in=6, keyword=True, tooling=True), P)
        self.assertAlmostEqual(tool, plain * 0.5)
        self.assertTrue(ev[0].startswith("Tooling path"))

    def test_business_without_flow_data_rescales_the_other_terms(self):
        with_flows, _ = aspects.business(facts(fan_in=6, keyword=True, share=0.25), P)
        no_flows, ev = aspects.business(facts(fan_in=6, keyword=True, share=0.25, flows_available=False), P)
        self.assertAlmostEqual(with_flows, 55.0)          # dependents, keyword and size at full strength; no flows
        self.assertAlmostEqual(no_flows, 100.0)           # the same terms carry the whole weight
        self.assertTrue(any("no execution-flow data" in e for e in ev))

    def test_entropy_without_functions_uses_neutral_terms(self):
        score, ev = aspects.entropy(facts(funcs=[], typed_share=None), P)
        self.assertTrue(any("No functions to measure" in e for e in ev))
        self.assertTrue(0 < score < 40)

    def test_effects_grow_with_writes_and_migrations(self):
        quiet, _ = aspects.effects(facts(), P)
        busy, ev = aspects.effects(facts(effect_counts=Counter(db_write=10), effect_examples={"db_write": "a.py:1"}, migrations=3, fan_in=4), P)
        self.assertGreater(busy, quiet + 40)
        self.assertTrue(any("migration" in e for e in ev))

    def test_unverified_rules(self):
        neutral, _, info = aspects.unverified(facts(graph_functions=0, linked_functions=0, linked_test_loc=0.0, test_scales=Counter()), 0, P)
        self.assertEqual((neutral, info["coverage"]), (P.unverified_no_functions, None))
        base, _, _ = aspects.unverified(facts(test_scales=Counter(unit=3)), 10, P)
        risky, ev, _ = aspects.unverified(facts(test_scales=Counter(unit=3)), 80, P)
        self.assertEqual(risky, base + P.no_integration_penalty)
        untested, _, _ = aspects.unverified(facts(linked_functions=0, linked_test_files=0, linked_test_loc=0.0, test_scales=Counter()), 0, P)
        self.assertGreaterEqual(untested, 99)
        na, ev, _ = aspects.unverified(facts(na_reason="Infrastructure manifest"), 90, P)
        self.assertIsNone(na)
        self.assertTrue(ev[0].startswith("Not applicable"))


if __name__ == "__main__":
    unittest.main()
