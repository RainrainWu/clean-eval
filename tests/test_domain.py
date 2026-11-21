"""The domain layer: data models whose invariants are pydantic validators. What they accept, refuse, derive and drop."""
import datetime as dt
import unittest

from pydantic import ValidationError

from helpers import facts, rating

from clean_eval.domain.change import ModuleRecord
from clean_eval.domain.ci import StepOutputs, ToolCall
from clean_eval.domain.errors import InvalidOutput, InvalidPolicy
from clean_eval.domain.history import FetchFailure, MergedPR, Period, ReviewSignal
from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import STRUCTURAL, TUNABLE, Policy
from clean_eval.domain.rating import Facts, Scores
from clean_eval.domain.review import AgentReview, CheckedRules, Finding, PullRequest, Review, ReviewRule
from clean_eval.domain.tuning import AgentEvalResult, EvalResult, ModelChange, PolicyEdit, Rejection, RulesChange
from clean_eval.domain.values import BAND_NAMES, Band

P = Policy()
SHA = "a" * 40


class PolicyTest(unittest.TestCase):
    def test_overrides_merge_and_are_checked(self):
        p = Policy.from_overrides({"business_weights": {"dependents": 0.4}, "unrated_band": "High", "_note": "comment"})
        self.assertEqual(p.business_weights["dependents"], 0.4)
        self.assertEqual(p.business_weights["size"], P.business_weights["size"])  # other keys keep their defaults
        self.assertEqual(p.unrated_band, "High")
        self.assertEqual(Policy.from_overrides({}), P)
        self.assertEqual(Policy.from_overrides(None), P)

    def test_divisors_must_stay_above_zero(self):
        for bad in ({"effects_scale": 0}, {"effects_dependents_saturation": 0}, {"business_saturation": {"dependents": 0}},
                    {"entropy_saturation": {"todo_per_k": 0}}, {"longest_range": 0}, {"avg_complexity_range": 0},
                    {"unverified_ratio_target": 0}):
            with self.assertRaises(InvalidPolicy, msg=repr(bad)) as cm:
                Policy.from_overrides(bad)
            self.assertIn("greater than 0", str(cm.exception))
        self.assertEqual(Policy.from_overrides({"business_weights": {"keyword": 0}}).business_weights["keyword"], 0)  # weights may be 0

    def test_invalid_overrides_are_refused(self):
        for bad in ({"nope": 1}, {"business_weights": {"unknown": 1}}, {"business_weights": {"dependents": -1}},
                    {"business_weights": {"dependents": True}}, {"unrated_band": "Huge"}, {"overall_peak_share": 2},
                    {"depth": 0}, {"depth": 1.5}, {"tooling": "("}, {"exclude": "vendor/*"}, {"not_applicable": [["x"]]},
                    {"not_applicable": [["reason", "("]]}, {"business_weights": {k: 0 for k in P.business_weights}},
                    {"version": "2"}, {"effects_scale": "40"}, {"effects_scale": float("nan")},
                    {"business_weights": {"size": float("inf")}}, [1]):
            with self.assertRaises(InvalidPolicy, msg=repr(bad)):
                Policy.from_overrides(bad)

    def test_messages_say_where_and_what(self):
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_overrides({"nope": 1, "depth": 0}, "risk-model.json")
        self.assertEqual(str(cm.exception), "risk-model.json: depth: Input should be greater than or equal to 1; nope: unknown key")
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_text("{not json", "risk-model.json")
        self.assertIn("risk-model.json is not valid JSON", str(cm.exception))

    def test_v1_model_files_get_a_migration_hint(self):
        with self.assertRaises(InvalidPolicy) as cm:
            Policy.from_overrides({"window_days": 30, "likelihood_weights": {}})
        self.assertIn("v1 model", str(cm.exception))

    def test_tunable_and_structural_knobs_partition_the_policy(self):
        self.assertFalse(TUNABLE & STRUCTURAL)
        self.assertIn("business_weights", TUNABLE)
        self.assertIn("depth", STRUCTURAL)
        self.assertNotIn("version", TUNABLE)

    def test_the_policy_is_immutable(self):
        with self.assertRaises(ValidationError):
            P.depth = 1


class RatingModels(unittest.TestCase):
    def test_scores_are_0_to_100_and_only_unverified_may_be_na(self):
        Scores(biz=0, effects=100, entropy=5, unverified=None, overall=60)
        for bad in ({"biz": 101}, {"entropy": -1}, {"biz": None}, {"overall": "high"}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                Scores(**{"biz": 1, "effects": 1, "entropy": 1, "unverified": 1, "overall": 1, **bad})

    def test_assignments_are_validated(self):
        m = rating()
        with self.assertRaises(ValidationError):
            m.scores.biz = 140
        with self.assertRaises(ValidationError):
            m.loc = -5

    def test_facts_keep_their_counters_through_json(self):
        f = facts()
        back = Facts.model_validate_json(f.model_dump_json())
        self.assertEqual(back, f)
        self.assertEqual(back.test_scales.most_common(1), [("unit", 2)])
        with self.assertRaises(ValidationError):
            facts(share=1.5)

    def test_rated_means_scored(self):
        ModuleRecord(module="m", band="Low", score=10, rated=True)
        ModuleRecord(module="infra", band="Moderate", rated=False)
        for bad in ({"rated": True}, {"rated": False, "score": 30}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                ModuleRecord(module="m", band="Low", **bad)

    def test_the_band_type_matches_the_bands(self):
        self.assertEqual(Band.__args__, BAND_NAMES)


class JudgementModel(unittest.TestCase):
    def test_invalid_entries_are_dropped_not_fatal(self):
        j = Judgement.model_validate({"modules": {
            "a": {"business": {"level": 1, "reason": "core path " * 50}, "effects": {"level": 9}, "nope": {"level": 0}},
            "b": {"entropy": {"level": True}}, "c": "x", 7: {"business": {"level": 0}}}})
        self.assertEqual(list(j.modules), ["a"])
        self.assertEqual(list(j.modules["a"]), ["business"])
        self.assertEqual(len(j.modules["a"]["business"].reason), 240)
        self.assertEqual(Judgement.model_validate("not an object").modules, {})


class ReviewModels(unittest.TestCase):
    def finding(self, **kw):
        return {"rule": "Q-02", "severity": "major", "file": "a.py", "line": 3, "summary": "s", **kw}

    def test_agent_findings_are_checked_field_by_field(self):
        for bad in ({"rule": "CA-01\noutput-dir=/x"}, {"rule": "lowercase-1"}, {"severity": "critical"}, {"line": "12"},
                    {"line": True}, {"line": 0}, {"line": 1.0}, {"file": "a\nb"}, {"file": 5}, {"summary": None}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                AgentReview.model_validate({"findings": [self.finding(**bad)]})

    def test_agent_text_is_cut_and_extras_are_ignored(self):
        r = AgentReview.model_validate({"verdict": "approve", "blockers": 0,
                                        "findings": [self.finding(file="", fix=None, summary="x" * 2000)] * 60})
        self.assertEqual(len(r.findings), 50)
        f = r.findings[0]
        self.assertEqual((f.file, f.fix, len(f.summary)), (None, "", 1500))
        self.assertEqual(AgentReview.model_validate({"findings": None}).findings, [])

    def test_the_verdict_and_counts_are_derived(self):
        review = Review(max_tier="High", max_score=52, findings=[Finding(rule="CA-01", severity="blocker"),
                                                                Finding(rule="Q-02", severity="major")])
        self.assertEqual((review.verdict, review.blockers, review.majors), ("request_changes", 1, 1))
        stored = {**review.model_dump(), "verdict": "approve", "blockers": 0}  # a tampered result.json
        self.assertEqual(Review.model_validate(stored).verdict, "request_changes")
        self.assertEqual(Review(max_tier="Low", max_score=0).verdict, "approve")
        with self.assertRaises(ValidationError):
            Review(max_tier="Huge", max_score=10)

    def test_rules_tables(self):
        self.assertEqual(ReviewRule(id="Q-02", severity="major", min_tier="Medium").min_tier, "Moderate")
        rule = {"id": "CA-01", "severity": "blocker", "min_tier": "Low"}
        CheckedRules(rules=[rule])
        for bad in ([], [rule, rule]):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                CheckedRules(rules=bad)

    def test_pull_request_commits_are_object_ids(self):
        pr = {"number": 7, "baseRefName": "main", "headRefName": "f", "baseRefOid": SHA, "headRefOid": "b" * 40,
              "author": {"login": "dev"}}
        self.assertEqual(PullRequest.model_validate(pr).author, "dev")
        for bad in ("HEAD", "a" * 39, "--upload-pack=x", "g" * 40):
            with self.assertRaises(ValidationError, msg=bad):
                PullRequest.model_validate({**pr, "headRefOid": bad})


class CiModels(unittest.TestCase):
    def test_step_outputs_are_single_lines(self):
        self.assertEqual(StepOutputs({"max-tier": "High", "auc": ""}).root, {"max-tier": "High", "auc": ""})
        for bad in ({"verdict": "approve\nresult=/etc/passwd"}, {"Bad Name": "x"}, {"auc": 0.5}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                StepOutputs(bad)

    def test_tool_calls(self):
        call = ToolCall.model_validate_json('{"tool_name": "Read", "tool_input": null, "session_id": "s"}')
        self.assertEqual((call.tool_name, call.tool_input, call.cwd), ("Read", {}, None))
        for bad in ('{"tool_name": ["Read"]}', '{"tool_input": "x"}', '[1, 2]', 'not json'):
            with self.assertRaises(ValidationError, msg=bad):
                ToolCall.model_validate_json(bad)


class HistoryModels(unittest.TestCase):
    def test_period(self):
        p = Period(since="2026-01-01", until="2026-01-31", window_days=10)
        self.assertEqual(p.lookahead_until, dt.date(2026, 2, 10))
        self.assertEqual(p.until_ts, dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp())
        for bad in ({"since": "2026-02-01", "until": "2026-01-01"}, {"since": "last week", "until": "2026-01-01"},
                    {"since": "2026-01-01", "until": "2026-02-30"}):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                Period(**bad)

    def test_merged_prs_keep_a_file_count_not_the_files(self):
        u = MergedPR(sha=SHA, base="b" * 40, author="a", ts=1, subject="s", files=["x.py", "y.py"])
        dumped = u.model_dump()
        self.assertNotIn("files", dumped)
        self.assertEqual(MergedPR.model_validate(dumped).file_count, 2)

    def test_a_fetch_failure_never_passes_for_a_review(self):
        failed = MergedPR.model_validate({"sha": SHA, "base": SHA, "author": "a", "ts": 1, "subject": "s",
                                          "review": {"error": "not_found", "message": "gone"}})
        self.assertIsInstance(failed.review, FetchFailure)
        reviewed = MergedPR.model_validate({"sha": SHA, "base": SHA, "author": "a", "ts": 1, "subject": "s",
                                            "review": {"rules_cited": ["CA-01"]}})
        self.assertIsInstance(reviewed.review, ReviewSignal)


class TuningModels(unittest.TestCase):
    def test_policy_edits(self):
        edit = PolicyEdit(new={"business_weights": {"dependents": 0.45}, "_why": "x"}, orig={"_why": "y"})
        self.assertEqual(edit.changed_keys, {"business_weights"})
        for new in ({"depth": 1}, {"business_weights": {"dependents": -1}}):
            with self.assertRaises(ValidationError, msg=repr(new)):
                PolicyEdit(new=new)

    def test_recommendations_are_text(self):
        r = AgentEvalResult.model_validate({"recommendations": [{"target": 1, "change": None, "evidence": "e" * 3000}, "junk"]
                                            + [{"target": "t"}] * 40, "auc": 0.99})
        self.assertEqual(len(r.recommendations), 30)
        first = r.recommendations[0]
        self.assertEqual((first.target, first.change, len(first.evidence)), ("1", "None", 2000))

    def test_applied_is_derived(self):
        base = dict(since="2026-01-01", until="2026-02-01", prs=30, defective_prs=6, auc=0.7)
        self.assertFalse(EvalResult(**base, model_change=Rejection(reason="no")).applied)
        self.assertTrue(EvalResult(**base, rules_change=RulesChange()).applied)
        accepted = EvalResult(**base, model_change=ModelChange(changed_keys=["x"], auc_before=0.6, auc_after=0.7))
        self.assertEqual(EvalResult.model_validate(accepted.model_dump()).model_change, accepted.model_change)


class ErrorMessages(unittest.TestCase):
    def test_validation_errors_read_as_where_and_what(self):
        try:
            AgentReview.model_validate({"findings": [{"rule": "x", "severity": "major"}]})
        except ValidationError as e:
            message = str(InvalidOutput.from_validation(e, "result.json"))
        self.assertTrue(message.startswith("result.json: findings[0].rule: String should match pattern"), message)


if __name__ == "__main__":
    unittest.main()
