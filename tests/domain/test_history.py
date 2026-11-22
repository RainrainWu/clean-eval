"""The merged-PR history models (domain/history.py): periods, merged PRs and their review signal."""
import datetime as dt
import unittest

from pydantic import ValidationError

from clean_eval.domain.history import FetchFailure, MergedPR, Period, ReviewSignal

SHA = "a" * 40


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


if __name__ == "__main__":
    unittest.main()
