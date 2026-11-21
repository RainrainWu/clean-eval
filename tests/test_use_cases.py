"""Use cases, run against in-memory fakes of the ports: no git, no files, no network."""
import unittest

from helpers import heatmap, rating, sf

from clean_eval.domain.entities import FileSignals, PyStats, Snapshot
from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.graph import Graph
from clean_eval.domain.policy import Policy
from clean_eval.domain.readme import MARKERS, fingerprint
from clean_eval.use_cases.assess_change import AssessChange
from clean_eval.use_cases.finalize_heatmap import finalize_heatmap
from clean_eval.use_cases.present_heatmap import BuildDiagram, RenderPage, SummarizeHeatmap
from clean_eval.use_cases.rate_repository import RateRepository
from clean_eval.use_cases.update_readme import UpdateReadme

P = Policy()
SNAP = Snapshot(repo="demo", ref="main", sha="abc1234", root="/r")


class FakeSource:
    def __init__(self, files):
        self.files = files

    def snapshot(self):
        return SNAP

    def read(self):
        return self.files


class FakeGraph:
    def __init__(self, graph):
        self.graph, self.calls = graph, 0

    def graph_for(self, snapshot, files):
        self.calls += 1
        return self.graph


class FakeSignals:
    def file_signals(self, f):
        return FileSignals(stats=PyStats(funcs=[(10, 2)], typed=1), todo=0, effects={"db_write": (2, 7)} if "core" in f.path else {})


class FakeCoverage:
    def line_rates(self, snapshot):
        return {}


def graph(has_flows=True):
    nodes = {"src/api/a.py::f": ("Function", "src/api/a.py", False), "src/core/c.py::g": ("Function", "src/core/c.py", False)}
    return Graph(nodes, {"src/api/a.py": {"src/core/c.py"}}, {"src/api/a.py::f": {"tests/unit/test_a.py"}},
                 [(0.9, "src/api/a.py", {"src/api/a.py", "src/core/c.py"})] if has_flows else [],
                 {"source": "fake", "nodes": 2, "edges": 1, "flows": 1 if has_flows else 0}, has_flows=has_flows)


def files():
    return [sf("src/api/a.py", 300), sf("src/api/b.py", 300), sf("src/core/c.py", 100), sf("tests/unit/test_a.py", 50, test=True)]


def rater(source_files=None, has_flows=True):
    return RateRepository(FakeSource(files() if source_files is None else source_files), FakeGraph(graph(has_flows)),
                          FakeSignals(), FakeCoverage())


class RateRepositoryTest(unittest.TestCase):
    def test_rates_every_module_from_the_ports(self):
        h = rater().execute(P, "2026-11-16", depth=2)
        self.assertEqual((h.repo, h.ref, h.sha, h.generated_at, h.policy_version), ("demo", "main", "abc1234", "2026-11-16", 2))
        self.assertEqual([m.name for m in h.modules], ["src/api", "src/core"])  # largest first; tests are not modules
        api, core = h.modules
        self.assertEqual((api.loc, api.tests["functions"], api.tests["tested"], api.tests["scales"]), (600, 1, 1, {"unit": 1}))
        self.assertEqual(api.baseline, {k: api.scores[k] for k in ("biz", "effects", "entropy", "unverified")})
        self.assertEqual(core.facts["effect_counts"], {"db_write": 2})
        self.assertEqual(core.facts["effect_examples"], {"db_write": "src/core/c.py:7"})
        self.assertEqual(core.facts["fan_in"], 1)
        self.assertTrue(h.notes[0].startswith("Knowledge graph (fake): 2 nodes"))
        self.assertEqual(h.loc_total, 700)

    def test_without_flows_the_facts_say_so(self):
        h = rater(has_flows=False).execute(P, "2026-11-16", depth=2)
        self.assertFalse(h.modules[0].facts["flows_available"])

    def test_excluded_and_missing_source(self):
        p = Policy.from_overrides({"exclude": ["src/core/*"]})
        names = [m.name for m in rater().execute(p, "d", depth=2).modules]
        self.assertTrue(names and not any("core" in n for n in names), names)  # excluded code is not rated
        for source in ([], [sf("tests/unit/test_a.py", 50, test=True)]):
            with self.assertRaises(NoPythonSource):
                rater(source).execute(P, "d")


class PresentationUseCases(unittest.TestCase):
    class Recorder:
        def present(self, heatmap, suffix):
            self.seen = (heatmap, suffix)
            return "<html/>"

        def svg(self, heatmap):
            return "<svg/>"

        def readme_block(self, heatmap, image_link, fp):
            return "%s\nimg %s fp %s\n%s\n" % (MARKERS[0], image_link, fp, MARKERS[1])

        def report(self, heatmap):
            return "report"

    def judged(self):
        return {"modules": {"m": {"business": {"level": 0, "reason": "r"}}}}

    def test_levels_are_applied_before_presenting(self):
        presenter = self.Recorder()
        page = RenderPage(presenter).execute(heatmap(rating()), self.judged(), P, " Detail")
        self.assertEqual((page.html, presenter.seen[1]), ("<html/>", " Detail"))
        self.assertGreaterEqual(presenter.seen[0].modules[0].scores["biz"], 75)
        built = BuildDiagram(self.Recorder()).execute(heatmap(rating()), None, P, "docs/x.svg")
        self.assertEqual(built.svg, "<svg/>")
        self.assertIn("img docs/x.svg fp %s" % built.fingerprint, built.readme_block)
        final, text = SummarizeHeatmap(self.Recorder()).execute(heatmap(rating()), self.judged(), P)
        self.assertEqual((text, final.judged_modules), ("report", 1))
        self.assertEqual(finalize_heatmap(heatmap(rating()), None, P).judged_modules, 0)


class FakeStore:
    def __init__(self, readme=None, svg=False):
        self.readme, self.svg, self.writes = readme, svg, []

    def read_readme(self):
        return self.readme

    def svg_exists(self):
        return self.svg

    def image_link(self):
        return "docs/risk-heatmap.svg"

    def write(self, readme_text, svg_text):
        self.writes.append((readme_text, svg_text))
        self.readme, self.svg = readme_text, True


class UpdateReadmeTest(unittest.TestCase):
    def test_writes_when_ratings_change_and_not_otherwise(self):
        store = FakeStore("# Demo\n")
        first = UpdateReadme(PresentationUseCases.Recorder(), store).execute(heatmap(rating()), None, P)
        self.assertEqual((first.changed, first.action, len(store.writes)), (True, "ADD", 1))
        self.assertIn("fp %s" % fingerprint(heatmap(rating())), store.readme)
        # the real block carries the fingerprint as a marker comment: simulate it so the next run can read it back
        store.readme = store.readme.replace("fp %s" % first.fingerprint, "<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint)
        again = UpdateReadme(PresentationUseCases.Recorder(), store).execute(heatmap(rating()), None, P)
        self.assertEqual((again.changed, len(store.writes)), (False, 1))
        store.svg = False  # diagram deleted: rewrite even though the ratings match
        self.assertTrue(UpdateReadme(PresentationUseCases.Recorder(), store).execute(heatmap(rating()), None, P).changed)
        moved = UpdateReadme(PresentationUseCases.Recorder(), store).execute(heatmap(rating(biz=90)), None, P)
        self.assertEqual((moved.changed, moved.action), (True, "UPDATE"))


class AssessChangeTest(unittest.TestCase):
    def test_change_risk_from_the_base_heat_map(self):
        heat, risk = AssessChange(rater()).execute(["src/core/c.py", "README.md"], P, "d")
        self.assertEqual([m.module for m in risk.modules], ["src/core"])
        self.assertEqual(risk.max_band, risk.modules[0].band)
        self.assertEqual(risk.ignored, ["README.md"])

    def test_a_base_without_python_rates_everything_unrated(self):
        heat, risk = AssessChange(rater([])).execute(["src/new.py"], P, "d")
        self.assertEqual((heat.modules, risk.max_band, risk.modules[0].rated), ([], "Moderate", False))


if __name__ == "__main__":
    unittest.main()
