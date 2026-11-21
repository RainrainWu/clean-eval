import datetime as dt
import json
import os
import unittest
from unittest import mock

from helpers import FakeBin, Repo
import history

from clean_eval.domain.policy import BAND_NAMES, Policy

D = lambda s: dt.datetime.fromisoformat(s + "T12:00:00+00:00")  # noqa: E731


def model(**overrides):
    return Policy.from_overrides(overrides)


class ParsingTest(unittest.TestCase):
    def test_pr_number(self):
        self.assertEqual(history.pr_number("Fix #45 (#67)"), 67)
        self.assertEqual(history.pr_number("Merge pull request #12 from a/b"), 12)
        self.assertEqual(history.pr_number("feat: thing (#478)"), 478)
        self.assertIsNone(history.pr_number("chore: no pr"))

    def test_rule_tags(self):
        text = "[CA-01][blocker] x SHA-256 [Q-01] [NEW] CA-02 [R-123]"
        self.assertEqual(history.RULE_ID.findall(text), ["CA-01", "Q-01", "NEW", "R-123"])

    def test_loads_multi_concatenated_pages(self):
        self.assertEqual(history._loads_multi('[{"a":1}][{"a":2}]\n[]'), [{"a": 1}, {"a": 2}])
        self.assertEqual(history._loads_multi('{"x": 1}'), {"x": 1})

    def test_auc(self):
        self.assertEqual(history.auc([0.9, 0.8], [0.1, 0.2]), 1.0)
        self.assertEqual(history.auc([0.5], [0.5]), 0.5)
        self.assertIsNone(history.auc([], [0.1]))


class CollectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = cls.repo = Repo()
        r.commit("init", {"src/billing/a.py": "a\n" * 20, "src/users/u.py": "u\n" * 20}, when=D("2026-01-01"))
        cls.feat = r.commit("feat: billing discounts (#1)", {"src/billing/a.py": "b\n" * 25}, when=D("2026-02-01"))
        r.commit("docs: notes (#2)", {"docs/n.md": "n\n"}, when=D("2026-02-02"))
        cls.users = r.commit("feat: users export (#4)", {"src/users/u.py": "v\n" * 22}, when=D("2026-02-03"))
        r.commit("fix: billing rounding (#3)", {"src/billing/a.py": "c\n" * 25}, when=D("2026-02-05"))
        r.commit('Revert "feat: users export (#4)" (#5)', {"src/users/u.py": "u\n" * 20}, when=D("2026-02-06"))
        cls.hist = history.collect(r.path, model(), "2026-01-15", "2026-02-28")
        cls.prs = {u["pr"]: u for u in cls.hist["prs"]}

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()

    def test_units_and_pr_numbers(self):
        self.assertEqual(sorted(self.prs), [1, 2, 3, 4, 5])
        self.assertEqual(self.hist["period"]["branch"], "main")

    def test_fix_attributed_heuristically(self):
        d = self.prs[1]["defects"]
        self.assertEqual(len(d), 1)
        self.assertEqual((d[0]["fix_pr"], d[0]["kind"], d[0]["confidence"]), (3, "fix", "heuristic"))
        self.assertEqual(d[0]["overlap_files"], ["src/billing/a.py"])

    def test_revert_attributed_with_high_confidence(self):
        d = self.prs[4]["defects"]
        self.assertEqual((d[0]["fix_pr"], d[0]["kind"], d[0]["confidence"]), (5, "revert", "high"))

    def test_docs_pr_has_no_modules_or_defects(self):
        self.assertEqual(self.prs[2]["modules"], [])
        self.assertEqual(self.prs[2]["defects"], [])

    def test_totals(self):
        self.assertEqual(self.hist["totals"], {"prs": 5, "defective_prs": 2, "escape_rate": 0.4})

    def test_prs_carry_the_base_heat_map(self):
        u = self.prs[1]
        self.assertIn(u["max_tier"], BAND_NAMES)
        billing = [m for m in u["modules"] if m["module"] == "src"][0]
        self.assertTrue(billing["rated"])
        self.assertEqual(set(billing["scores"]), {"biz", "effects", "entropy", "unverified", "overall"})
        self.assertEqual(billing["score"], billing["scores"]["overall"])
        self.assertEqual(billing["facts"]["name"], "src")

    def test_replay_reproduces_and_sensitivity_varies_the_policy(self):
        same = history.replay(self.hist, model())
        self.assertEqual(set(same), {"auc_max_score", "by_band", "band_mix"})
        self.assertEqual(same["by_band"], self.hist["calibration"]["by_band"])  # facts are stored exactly
        rows = history.sensitivity(self.hist, model())
        self.assertEqual(rows[0]["change"], "baseline")
        changes = [r["change"] for r in rows]
        self.assertEqual(len(changes), len(set(changes)))
        self.assertTrue(any(c.startswith("business_weights.dependents") for c in changes))
        self.assertTrue(any(c.startswith("overall_peak_share") for c in changes))
        self.assertTrue(any(c.startswith("unverified_mix.links") for c in changes))

    def test_rescore_unrated_modules_take_the_policy_band(self):
        prs = history.rescore([{"modules": [{"module": "infra", "rated": False, "scores": {}, "score": None, "band": "Moderate"}],
                                "defects": []}], model(unrated_band="High"))
        self.assertEqual((prs[0]["max_tier"], prs[0]["max_score"]), ("High", 50))


class GhReviewDataTest(unittest.TestCase):
    def setUp(self):
        self.bin = FakeBin()
        self.env = mock.patch.dict(os.environ, self.bin.env())
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.bin.cleanup()

    def test_rules_counted_from_clean_eval_reviewer_only(self):
        self.bin.gh({"pr_view": {"out": {
            "url": "u", "labels": [], "additions": 1, "deletions": 1,
            "reviews": [{"author": {"login": "clean-eval[bot]"},
                         "body": "[CA-01][blocker] x\n<!-- clean-eval:pr-review tier=High -->"},
                        {"author": {"login": "human"}, "body": "also [Q-02] maybe <!-- clean-eval:pr-review tier=Low -->"}],
            "comments": []}},
            "inline_comments": {"out": '[{"user":{"login":"clean-eval[bot]"},"body":"[NEW][major] y"}]'
                                       '[{"user":{"login":"clean-eval[bot]"},"body":"[R-03][blocker] z"}]'}})
        d = history.gh_review_data(7)
        self.assertEqual(d["rules_cited"], ["CA-01", "R-03"])  # human's [Q-02] ignored; 2nd page parsed
        self.assertEqual(d["new_findings"], 1)
        self.assertEqual(d["clean_eval_marker"], {"tier": "High"})  # the human's forged marker didn't count
        self.assertEqual(d["untrusted_markers_ignored"], 1)
        pinned = history.gh_review_data(7, reviewer_logins=["human"])
        self.assertEqual(pinned["clean_eval_marker"], {"tier": "Low"})

    def test_errors_are_classified(self):
        self.bin.gh({"pr_view_8": {"exit": 1, "err": "GraphQL: Could not resolve to a PullRequest with the number of 8."},
                     "pr_view": {"exit": 1, "err": "HTTP 502: Bad Gateway"}})
        self.assertEqual(history.gh_review_data(8)["error"], "not_found")
        self.assertEqual(history.gh_review_data(9)["error"], "error")

    def test_collect_with_gh_flags_unreliable_rules(self):
        r = Repo()
        try:
            r.commit("init", {"src/a/x.py": "1\n"}, when=D("2026-01-01"))
            r.commit("feat: a (#1)", {"src/a/x.py": "2\n"}, when=D("2026-02-01"))
            r.commit("feat: b (#2)", {"src/a/x.py": "3\n"}, when=D("2026-02-02"))
            self.bin.gh({"auth": {"exit": 0}, "pr_view": {"exit": 1, "err": "HTTP 403: rate limit exceeded"}})
            with mock.patch("sys.stderr"):
                h = history.collect(r.path, model(), "2026-01-15", "2026-02-28", with_gh=True)
            self.assertEqual(sorted(h["rules"]["prs_fetch_failed"]), [1, 2])
            self.assertFalse(h["rules"]["reliable"])
            self.assertEqual(h["rules"]["prs_reviewed"], 0)
        finally:
            r.cleanup()

    def test_collect_with_gh_requires_auth(self):
        self.bin.gh({"auth": {"exit": 1, "err": "not logged in"}})
        with self.assertRaises(SystemExit):
            history.collect(".", model(), "2026-01-01", "2026-01-02", with_gh=True)


if __name__ == "__main__":
    unittest.main()
