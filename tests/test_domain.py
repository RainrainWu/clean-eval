"""Domain rules: policy, scoring, bands, module splitting and lookup, the four aspects, classification, judgement, change
assessment and the README convention."""
import unittest
from collections import Counter

from helpers import facts, heatmap, rating, sf

from clean_eval.domain import aspects, classify
from clean_eval.domain.change import assess_change
from clean_eval.domain.entities import facts_from_dict, facts_to_dict
from clean_eval.domain.errors import InvalidPolicy
from clean_eval.domain.graph import Graph
from clean_eval.domain.judgement import apply_levels, refresh_overall
from clean_eval.domain.modules import module_of, split_modules
from clean_eval.domain.policy import ASPECTS, STRUCTURAL, TUNABLE, Policy
from clean_eval.domain.rating import rate_module, rescore
from clean_eval.domain.readme import MARKERS, SECTION_HEADING, block_fingerprint, fingerprint, merge_readme
from clean_eval.domain.scoring import band, band_floor, level_range, overall, place_in_level

P = Policy()


class PolicyTest(unittest.TestCase):
    def test_overrides_merge_and_are_checked(self):
        p = Policy.from_overrides({"business_weights": {"dependents": 0.4}, "unrated_band": "High", "_note": "comment"})
        self.assertEqual(p.business_weights["dependents"], 0.4)
        self.assertEqual(p.business_weights["size"], P.business_weights["size"])  # other keys keep their defaults
        self.assertEqual(p.unrated_band, "High")
        self.assertEqual(Policy.from_overrides({}), P)
        self.assertEqual(Policy.from_overrides(None), P)

    def test_invalid_overrides_are_refused(self):
        for bad in ({"nope": 1}, {"business_weights": {"unknown": 1}}, {"business_weights": {"dependents": -1}},
                    {"business_weights": {"dependents": True}}, {"unrated_band": "Huge"}, {"overall_peak_share": 2},
                    {"depth": 0}, {"depth": 1.5}, {"tooling": "("}, {"exclude": "vendor/*"}, {"not_applicable": [["x"]]},
                    {"business_weights": {k: 0 for k in P.business_weights}}, [1]):
            with self.assertRaises(InvalidPolicy, msg=repr(bad)):
                Policy.from_overrides(bad)

    def test_v1_model_files_get_a_migration_hint(self):
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_overrides({"window_days": 30, "likelihood_weights": {}})
        self.assertIn("v1 model", str(cm.exception))

    def test_tunable_and_structural_knobs_partition_the_policy(self):
        self.assertFalse(TUNABLE & STRUCTURAL)
        self.assertIn("business_weights", TUNABLE)
        self.assertIn("depth", STRUCTURAL)
        self.assertNotIn("version", TUNABLE)


class Scoring(unittest.TestCase):
    def test_one_outstanding_aspect_lifts_overall(self):
        self.assertEqual(overall({"biz": 95, "effects": 0, "entropy": 0, "unverified": 0}), 48)
        self.assertEqual(overall({"biz": 0, "effects": 0, "entropy": 0, "unverified": 100}), 50)

    def test_peak_share_moves_weight_to_the_worst_aspect(self):
        s = {"biz": 95, "effects": 0, "entropy": 0, "unverified": 0}
        self.assertEqual(overall(s, peak_share=1.0), 95)
        self.assertEqual(overall(s, peak_share=0.0), 0)

    def test_not_applicable_aspect_is_left_out_of_overall(self):
        self.assertEqual(overall({"biz": 40, "effects": 10, "entropy": 20, "unverified": None}),
                         overall({"biz": 40, "effects": 10, "entropy": 20, "unverified": 20}))

    def test_levels_map_to_bands_most_severe_first(self):
        self.assertEqual([level_range(n) for n in range(4)], [(75, 100), (50, 74), (25, 49), (0, 24)])
        self.assertEqual([place_in_level(0, s) for s in (0, 100)], [75, 100])
        self.assertEqual([band(place_in_level(n, 55)) for n in range(4)], ["Critical", "High", "Moderate", "Low"])

    def test_band_edges_and_floors(self):
        self.assertEqual([band(v) for v in (0, 24, 25, 49, 50, 74, 75, 100)],
                         ["Low", "Low", "Moderate", "Moderate", "High", "High", "Critical", "Critical"])
        self.assertEqual([band_floor(b) for b in ("Low", "Moderate", "High", "Critical")], [0, 25, 50, 75])


class Modules(unittest.TestCase):
    def test_large_module_is_split_into_children(self):
        mods, notes = split_modules([sf("a/b/x.py", 400), sf("a/c/y.py", 400), sf("z/w.py", 100)], P, depth=2)
        self.assertEqual(sorted(mods), ["a/b", "a/c", "z"])
        self.assertTrue(any("split a" in n for n in notes))

    def test_single_child_chain_is_skipped(self):
        mods, _ = split_modules([sf("pkg/src/pkg/one/a.py", 300), sf("pkg/src/pkg/two/b.py", 300), sf("other/c.py", 50)], P, depth=2)
        self.assertIn("pkg/src/pkg/one", mods)
        self.assertIn("pkg/src/pkg/two", mods)

    def test_depth_and_cap(self):
        files = [sf("src/ext/a.py", 100), sf("src/ext/b.py", 100), sf("src/ext/c.py", 100), sf("src/dom/d.py", 60), sf("src/dom/e.py", 60)]
        self.assertEqual(sorted(split_modules(files, P, depth=1)[0]), ["src"])
        self.assertEqual(sorted(split_modules(files, P, depth=2)[0]), ["src/dom", "src/ext"])
        self.assertEqual(sorted(split_modules(files, P)[0]), ["src/dom", "src/ext/a.py", "src/ext/b.py", "src/ext/c.py"])
        self.assertEqual(sorted(split_modules(files, P, depth=3, max_modules=2)[0]), ["src/dom", "src/ext"])

    def test_module_of_finds_the_deepest_module(self):
        names = {"src/api", "src/ext/a.py", "src/core/(files)", "src/core/deep", "(root)", "tools"}
        self.assertEqual(module_of("src/api/handlers/x.py", names), "src/api")
        self.assertEqual(module_of("src/ext/a.py", names), "src/ext/a.py")
        self.assertIsNone(module_of("src/ext/new.py", names))  # a new file in a folder split per file
        self.assertEqual(module_of("src/core/c.py", names), "src/core/(files)")
        self.assertEqual(module_of("src/core/deep/d.py", names), "src/core/deep")
        self.assertEqual(module_of("setup.py", names), "(root)")
        self.assertEqual(module_of("tools/x/y.sh", names), "tools")
        self.assertIsNone(module_of("terraform/main.tf", names))
        self.assertIsNone(module_of("setup.py", {"src"}))


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


class RatingTest(unittest.TestCase):
    def test_facts_round_trip_and_rescore_reproduce_the_rating(self):
        f = facts(effect_counts=Counter(db_write=2), test_scales=Counter(unit=1), funcs=[(70, 18), (5, 1)])
        r = rate_module(f, [], P)
        self.assertEqual(facts_from_dict(facts_to_dict(f)), f)
        self.assertEqual(rescore(r.facts, P), r.scores)
        louder = Policy.from_overrides({"effect_points": {"db_write": 30}})
        self.assertGreater(rescore(r.facts, louder)["effects"], r.scores["effects"])


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
        self.assertTrue(classify.ignored("README.md", P) and classify.ignored("docs/guide/x.py", P))
        self.assertFalse(classify.ignored("requirements.txt", P))  # dependency changes carry risk
        p = Policy.from_overrides({"exclude": ["vendor/*"]})
        self.assertTrue(classify.excluded("vendor/lib/x.py", p) and not classify.excluded("src/x.py", p))


class Judgement(unittest.TestCase):
    def test_every_aspect_level_sets_its_band_and_the_static_score_places_inside_it(self):
        for name, key, *_ in ASPECTS:
            for level, (lo, hi) in enumerate(((75, 100), (50, 74), (25, 49), (0, 24))):
                h = apply_levels(heatmap(rating()), {"modules": {"m": {name: {"level": level, "reason": "r"}}}})
                m = h.modules[0]
                self.assertTrue(lo <= m.scores[key] <= hi, (name, level, m.scores[key]))
                self.assertTrue(m.evidence[key][0].startswith("Level %d" % level))
        m = apply_levels(heatmap(rating()), {"modules": {"m": {"business": {"level": 1, "reason": "core"}}}}).modules[0]
        self.assertEqual((m.scores["biz"], band(m.scores["biz"])), (60, "High"))

    def test_invalid_judgements_are_ignored(self):
        for bad in ("high", 4, -1, 1.5, True, None, [1], {"a": 1}):
            h = apply_levels(heatmap(rating()), {"modules": {"m": {"business": {"level": bad}}, "nope": {"business": {"level": 0}}}})
            self.assertEqual(h.modules[0].scores["biz"], 40, bad)
            self.assertEqual(h.judged_modules, 0)
        for bad in ([], "x", {"modules": []}, {"modules": {"m": "x"}}):
            self.assertEqual(apply_levels(heatmap(rating()), bad).judged_modules, 0)
        h = apply_levels(heatmap(rating(unverified=None)), {"modules": {"m": {"unverified": {"level": 0}}}})
        self.assertIsNone(h.modules[0].scores["unverified"])

    def test_overall_is_refreshed_with_the_policy_share(self):
        h = heatmap(rating())
        h.modules[0].scores["overall"] = 0
        self.assertEqual(refresh_overall(h, 0.5).modules[0].scores["overall"], overall(h.modules[0].scores))


class ChangeAssessment(unittest.TestCase):
    def test_paths_take_their_module_band_and_unknown_paths_the_unrated_band(self):
        h = heatmap(rating("src/api", 500, biz=90, unverified=90), rating("src/util", 100, biz=5, effects=0, entropy=5, unverified=5))
        risk = assess_change(h, ["src/api/x.py", "src/util/y.py", "README.md", "terraform/main.tf", "src/new/z.py"], P)
        self.assertEqual(risk.files["src/api/x.py"], "src/api")
        self.assertEqual(risk.ignored, ["README.md"])
        mods = {m.module: m for m in risk.modules}
        self.assertEqual(mods["src/util"].band, "Low")
        self.assertFalse(mods["terraform"].rated)
        self.assertEqual((mods["terraform"].band, mods["src/new"].band), ("Moderate", "Moderate"))
        self.assertEqual(risk.max_band, band(mods["src/api"].score))
        self.assertEqual(risk.modules[0].module, "src/api")  # most severe first

    def test_docs_only_and_empty_changes_are_low(self):
        self.assertEqual(assess_change(heatmap(), ["docs/a.md"], P).max_band, "Low")
        self.assertEqual(assess_change(heatmap(), [], P).max_score, 0)

    def test_unrated_only_change_scores_the_band_floor(self):
        risk = assess_change(heatmap(), ["infra/main.tf"], Policy.from_overrides({"unrated_band": "High"}))
        self.assertEqual((risk.max_band, risk.max_score), ("High", 50))


class ReadmeConvention(unittest.TestCase):
    BLOCK = MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"

    def test_block_is_appended_with_a_heading_or_replaced_between_markers(self):
        text, action = merge_readme("# Title\n\nIntro.\n", self.BLOCK)
        self.assertEqual(action, "ADD")
        self.assertTrue(text.startswith("# Title\n\nIntro.\n\n" + SECTION_HEADING + "\n\n"))
        old = "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"
        text, action = merge_readme(old, self.BLOCK)
        self.assertEqual(action, "UPDATE")
        self.assertTrue("new" in text and "old table" not in text and text.endswith("\n\n## Next\nkeep\n"))
        self.assertEqual(merge_readme(None, self.BLOCK)[0], SECTION_HEADING + "\n\n" + self.BLOCK)

    def test_heading_without_markers_gets_its_body_replaced(self):
        text, action = merge_readme("# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n", self.BLOCK)
        self.assertEqual(action, "UPDATE")
        self.assertNotIn("stale", text)
        self.assertEqual(text.count(SECTION_HEADING), 1)
        self.assertTrue(text.endswith("## Next\nkeep\n"))

    def test_fingerprint_ignores_commit_and_date_but_not_scores(self):
        a, b = heatmap(rating()), heatmap(rating())
        b.sha, b.generated_at = "fff0000", "2030-01-01"
        self.assertEqual(fingerprint(a), fingerprint(b))
        self.assertNotEqual(fingerprint(a), fingerprint(heatmap(rating(biz=41))))
        self.assertEqual(block_fingerprint("x\n" + self.BLOCK), "0123456789ab")
        self.assertIsNone(block_fingerprint("no block"))


class GraphEntity(unittest.TestCase):
    def test_dependents_functions_and_test_files_by_module(self):
        nodes = {"a.py::f": ("Function", "a.py", False), "b.py::g": ("Function", "b.py", False), "c.py::t": ("Function", "c.py", True)}
        g, file_mod = Graph(nodes, {"a.py": {"b.py"}}, {"a.py::f": {"tests/test_a.py"}}, [], {}), {"a.py": "A", "b.py": "B", "c.py": "C"}
        fan_in, fan_out = g.dependents(file_mod)
        self.assertEqual((fan_in["B"], fan_out["A"]), ({"A"}, {"B"}))
        self.assertEqual(sorted(g.functions_by_module(file_mod)), ["A", "B"])
        self.assertEqual(g.test_files_to_modules(file_mod), {"tests/test_a.py": {"A"}})


if __name__ == "__main__":
    unittest.main()
