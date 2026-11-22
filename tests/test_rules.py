"""Business rules (use_cases/rules): scoring, bands, module splitting and lookup, the four aspects, rating,
classification, judgement, change risk, the README convention, the graph queries, the built-in knowledge graph and the
per-file signals."""
import unittest
from collections import Counter

from helpers import facts, heatmap, rating, sf

from clean_eval.domain.graph import Graph
from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import ASPECTS, Policy
from clean_eval.domain.rating import AspectScores, Facts
from clean_eval.use_cases.rules import aspects, classify
from clean_eval.use_cases.rules.ast_graph import builtin_graph
from clean_eval.use_cases.rules.change import change_risk
from clean_eval.use_cases.rules.graph import dependents, functions_by_module, test_files_to_modules
from clean_eval.use_cases.rules.judgement import apply_levels, refresh_overall
from clean_eval.use_cases.rules.modules import module_of, split_modules
from clean_eval.use_cases.rules.python_signals import file_signals, python_stats, write_calls
from clean_eval.use_cases.rules.rating import rate_module, rescore
from clean_eval.use_cases.rules.readme import MARKERS, SECTION_HEADING, block_fingerprint, fingerprint, merge_readme
from clean_eval.use_cases.rules.scoring import band, band_floor, level_range, overall, place_in_level

P = Policy()


def S(biz, effects, entropy, unverified):
    return AspectScores(biz=biz, effects=effects, entropy=entropy, unverified=unverified)


def levels(raw):
    return Judgement.model_validate(raw)


class Scoring(unittest.TestCase):
    def test_one_outstanding_aspect_lifts_overall(self):
        self.assertEqual(overall(S(95, 0, 0, 0)), 48)
        self.assertEqual(overall(S(0, 0, 0, 100)), 50)

    def test_peak_share_moves_weight_to_the_worst_aspect(self):
        s = S(95, 0, 0, 0)
        self.assertEqual(overall(s, peak_share=1.0), 95)
        self.assertEqual(overall(s, peak_share=0.0), 0)

    def test_not_applicable_aspect_is_left_out_of_overall(self):
        self.assertEqual(overall(S(40, 10, 20, None)), overall(S(40, 10, 20, 20)))

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
    def test_stored_facts_reproduce_the_rating(self):
        f = facts(effect_counts=Counter(db_write=2), test_scales=Counter(unit=1), funcs=[(70, 18), (5, 1)])
        r = rate_module(f, [], P)
        stored = Facts.model_validate_json(r.facts.model_dump_json())  # as data.json or a history keeps them
        self.assertEqual(stored, f)
        self.assertEqual(rescore(stored, P), r.scores)
        self.assertEqual(r.baseline.model_dump(), r.scores.model_dump(exclude={"overall"}))
        louder = Policy.from_overrides({"effect_points": {"db_write": 30}})
        self.assertGreater(rescore(stored, louder).effects, r.scores.effects)

    def test_scores_stay_in_range_when_tuned_weights_sum_past_one(self):
        heavy = Policy.from_overrides({"business_weights": {"dependents": 0.9, "keyword": 0.9, "size": 0.9}})
        r = rate_module(facts(fan_in=12, keyword=True, share=1.0), [], heavy)
        self.assertEqual(r.scores.biz, 100)


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


class JudgementRule(unittest.TestCase):
    def test_every_aspect_level_sets_its_band_and_the_static_score_places_inside_it(self):
        for name, key, *_ in ASPECTS:
            for level, (lo, hi) in enumerate(((75, 100), (50, 74), (25, 49), (0, 24))):
                h = apply_levels(heatmap(rating()), levels({"modules": {"m": {name: {"level": level, "reason": "r"}}}}))
                m = h.modules[0]
                self.assertTrue(lo <= getattr(m.scores, key) <= hi, (name, level, getattr(m.scores, key)))
                self.assertTrue(getattr(m.evidence, key)[0].startswith("Level %d" % level))
        m = apply_levels(heatmap(rating()), levels({"modules": {"m": {"business": {"level": 1, "reason": "core"}}}})).modules[0]
        self.assertEqual((m.scores.biz, band(m.scores.biz)), (60, "High"))

    def test_invalid_judgements_are_ignored(self):
        for bad in ("high", 4, -1, 1.5, True, None, [1], {"a": 1}):
            h = apply_levels(heatmap(rating()), levels({"modules": {"m": {"business": {"level": bad}},
                                                                    "nope": {"business": {"level": 0}}}}))
            self.assertEqual(h.modules[0].scores.biz, 40, bad)
            self.assertEqual(h.judged_modules, 0)
        for bad in ([], "x", {"modules": []}, {"modules": {"m": "x"}}):
            self.assertEqual(apply_levels(heatmap(rating()), levels(bad)).judged_modules, 0)
        h = apply_levels(heatmap(rating(unverified=None)), levels({"modules": {"m": {"unverified": {"level": 0}}}}))
        self.assertIsNone(h.modules[0].scores.unverified)

    def test_overall_is_refreshed_with_the_policy_share(self):
        h = heatmap(rating())
        h.modules[0].scores.overall = 0
        self.assertEqual(refresh_overall(h, 0.5).modules[0].scores.overall, overall(h.modules[0].scores))


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


class GraphQueries(unittest.TestCase):
    def test_dependents_functions_and_test_files_by_module(self):
        nodes = {"a.py::f": ("Function", "a.py", False), "b.py::g": ("Function", "b.py", False), "c.py::t": ("Function", "c.py", True)}
        g = Graph(nodes=nodes, links={"a.py": {"b.py"}}, tested={"a.py::f": {"tests/test_a.py"}}, flows=[], stats={})
        file_mod = {"a.py": "A", "b.py": "B", "c.py": "C"}
        fan_in, fan_out = dependents(g, file_mod)
        self.assertEqual((fan_in["B"], fan_out["A"]), ({"A"}, {"B"}))
        self.assertEqual(sorted(functions_by_module(g, file_mod)), ["A", "B"])
        self.assertEqual(test_files_to_modules(g, file_mod), {"tests/test_a.py": {"A"}})



SAMPLE = '''
def typed(a: int) -> int:
    if a and a > 1:
        return a
    return 0


def loose(x):
    try:
        return x
    except Exception:
        pass
'''


class PythonSignalsTest(unittest.TestCase):
    def test_python_stats(self):
        st = python_stats(SAMPLE)
        self.assertEqual((len(st.funcs), st.typed, st.broad, st.swallow), (2, 1, 1, 1))
        self.assertEqual(st.funcs[0][1], 3)  # one if and one boolean operator on top of the base path
        self.assertIsNone(python_stats("def broken(:"))

    def test_write_calls_are_counted_with_a_line_and_gated_by_context(self):
        aws = "import boto3\nc = boto3.client('s3')\nc.put_object(Bucket='b')\nc.delete_object(Bucket='b')\n"
        self.assertEqual(write_calls(aws)["aws_write"], (2, 3))
        self.assertNotIn("aws_write", write_calls("x.put_object(1)\n"))  # no boto import: not an AWS write

    def test_signals_are_cached_per_content(self):
        cache = {}
        a = file_signals(sf("a.py", 1, "# TODO a\nx = 1  # FIXME b\nTODOS = 3\n", blob="b1"), cache)
        self.assertEqual(a.todo, 2)
        self.assertIs(file_signals(sf("renamed.py", 1, "ignored", blob="b1"), cache), a)


class BuiltinGraphTest(unittest.TestCase):
    FILES = {
        "src/pkg/__init__.py": "from .core import run\n",
        "src/pkg/core.py": "import json\nfrom . import util\n\ndef run():\n    return util.helper()\n\nclass Svc:\n    def go(self):\n        pass\n",
        "src/pkg/util.py": "def helper():\n    return 1\n",
        "tools/json.py": "X = 1\n",
        "lambdas/x/handler.py": "import utils\n\ndef handler(e, c):\n    return utils.f()\n",
        "lambdas/x/utils.py": "def f():\n    pass\n",
        "other/deep/utils.py": "def f():\n    pass\n",
        "tests/test_core.py": "from pkg import run\nfrom pkg.core import Svc\n\ndef test_run():\n    assert run() and Svc().go() is None\n",
    }

    def graph(self, cache=None):
        return builtin_graph([sf(p, 1, t, test=p.startswith("tests/"), blob="blob-" + p) for p, t in self.FILES.items()], cache)

    def test_imports_resolve_by_path(self):
        g = self.graph()
        self.assertEqual(g.links["src/pkg/core.py"], {"src/pkg/util.py"})              # relative; stdlib json skipped
        self.assertEqual(g.links["src/pkg/__init__.py"], {"src/pkg/core.py"})
        self.assertEqual(g.links["tests/test_core.py"], {"src/pkg/__init__.py", "src/pkg/core.py"})  # src layout
        self.assertEqual(g.links["lambdas/x/handler.py"], {"lambdas/x/utils.py"})      # sibling, not other/deep
        self.assertFalse(g.has_flows)
        self.assertEqual(g.stats["source"], "built-in ast")

    def test_tests_link_to_the_functions_they_import_and_name(self):
        g = self.graph()
        self.assertEqual(g.tested["src/pkg/core.py::run"], {"tests/test_core.py"})      # through the __init__ re-export
        self.assertEqual(g.tested["src/pkg/core.py::Svc.go"], {"tests/test_core.py"})
        self.assertNotIn("src/pkg/util.py::helper", g.tested)
        self.assertEqual(g.nodes["src/pkg/core.py::Svc.go"], ("Function", "src/pkg/core.py", False))

    def test_per_file_syntax_is_cached(self):
        cache = {}
        self.graph(cache)
        self.assertEqual(len(cache), len(self.FILES))
        before = dict(cache)
        self.graph(cache)
        self.assertEqual(cache, before)


if __name__ == "__main__":
    unittest.main()
