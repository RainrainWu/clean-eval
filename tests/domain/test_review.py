"""The review models (domain/review.py): what an agent's proposed review may hold, and the review CI derives."""
import unittest

from pydantic import ValidationError

from clean_eval.domain.review import AgentReview, CheckedRules, Finding, PullRequest, Review, ReviewRule

SHA = "a" * 40


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


if __name__ == "__main__":
    unittest.main()
