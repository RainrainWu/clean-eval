"""Collecting the merged-PR history of a period (use_cases/collect_history.py): every PR rated at its base, escaped
defects attributed, and each PR's review signal from the code host, through fakes of the Git, Clock and CodeHost
interfaces."""

import datetime as dt

import pytest

from clean_eval.domain.errors import HeatmapError, RemoteError
from clean_eval.domain.history import FetchFailure, Period, ReviewSignal
from clean_eval.domain.policy import BAND_NAMES, Policy
from clean_eval.use_cases.collect_history import collect_history, review_of
from clean_eval.use_cases.interfaces import Clock, CodeHost
from clean_eval.use_cases.rules import calibration, history
from tests.use_cases.fakes import MemoryGit, fake

CLOCK = fake(Clock, today=lambda: "2026-03-01")
PERIOD = Period(since="2026-01-15", until="2026-02-28")


def day(s):
    return dt.datetime.fromisoformat(s + "T12:00:00+00:00")


def model(**overrides):
    return Policy.from_overrides(overrides)


def host(review_threads=lambda pr: ({}, []), auth_error=None):
    """A CodeHost that answers review_threads(pr) with `review_threads`, and fails require_auth with `auth_error`."""

    def require_auth():
        if auth_error:
            raise auth_error

    return fake(CodeHost, require_auth=require_auth, review_threads=review_threads)


@pytest.fixture(scope="module")
def collected():
    """The history of a period with a fix, a revert and a docs-only PR (built once for the module)."""
    git = MemoryGit()
    git.commit("init", {"src/billing/a.py": "a\n" * 20, "src/users/u.py": "u\n" * 20}, when=day("2026-01-01"))
    git.commit("feat: billing discounts (#1)", {"src/billing/a.py": "b\n" * 25}, when=day("2026-02-01"))
    git.commit("docs: notes (#2)", {"docs/n.md": "n\n"}, when=day("2026-02-02"))
    git.commit("feat: users export (#4)", {"src/users/u.py": "v\n" * 22}, when=day("2026-02-03"))
    git.commit("fix: billing rounding (#3)", {"src/billing/a.py": "c\n" * 25}, when=day("2026-02-05"))
    git.commit('Revert "feat: users export (#4)" (#5)', {"src/users/u.py": "u\n" * 20}, when=day("2026-02-06"))
    return collect_history(git, CLOCK, model(), PERIOD)


@pytest.fixture(scope="module")
def hist(collected):
    """eval-history.json, as the eval agent reads it."""
    return collected.model_dump(mode="json")


@pytest.fixture(scope="module")
def prs(hist):
    return {u["pr"]: u for u in hist["prs"]}


def test_units_and_pr_numbers(hist, prs):
    assert sorted(prs) == [1, 2, 3, 4, 5]
    assert hist["period"]["branch"] == "main"


def test_fix_attributed_heuristically(prs):
    d = prs[1]["defects"]
    assert len(d) == 1
    assert (d[0]["fix_pr"], d[0]["kind"], d[0]["confidence"]) == (3, "fix", "heuristic")
    assert d[0]["overlap_files"] == ["src/billing/a.py"]


def test_revert_attributed_with_high_confidence(prs):
    d = prs[4]["defects"]
    assert (d[0]["fix_pr"], d[0]["kind"], d[0]["confidence"]) == (5, "revert", "high")


def test_docs_pr_has_no_modules_or_defects(prs):
    assert prs[2]["modules"] == []
    assert prs[2]["defects"] == []


def test_totals(hist):
    assert hist["totals"] == {"prs": 5, "defective_prs": 2, "escape_rate": 0.4}


def test_prs_carry_the_base_heat_map(prs):
    u = prs[1]
    assert u["max_tier"] in BAND_NAMES
    billing = [m for m in u["modules"] if m["module"] == "src"][0]
    assert billing["rated"]
    assert set(billing["scores"]) == {"biz", "effects", "entropy", "unverified", "overall"}
    assert billing["score"] == billing["scores"]["overall"]
    assert billing["facts"]["name"] == "src"


def test_replay_reproduces_and_sensitivity_varies_the_policy(collected):
    same = calibration.replay(collected, model())
    assert set(same.model_dump()) == {"auc_max_score", "by_band", "band_mix"}
    assert same.by_band == collected.calibration.by_band  # facts are stored exactly
    rows = calibration.sensitivity(collected, model())
    assert rows[0].change == "baseline"
    changes = [r.change for r in rows]
    assert len(changes) == len(set(changes))
    assert any(c.startswith("business_weights.dependents") for c in changes)
    assert any(c.startswith("overall_peak_share") for c in changes)
    assert any(c.startswith("unverified_mix.links") for c in changes)


VIEW = {
    "url": "u",
    "labels": [],
    "additions": 1,
    "deletions": 1,
    "comments": [],
    "reviews": [
        {
            "author": {"login": "clean-eval[bot]"},
            "body": "[CA-01][blocker] x\n<!-- clean-eval:pr-review tier=High -->",
        },
        {"author": {"login": "human"}, "body": "also [Q-02] maybe <!-- clean-eval:pr-review tier=Low -->"},
    ],
}
INLINE = [
    {"user": {"login": "clean-eval[bot]"}, "body": "[NEW][major] y"},
    {"user": {"login": "clean-eval[bot]"}, "body": "[R-03][blocker] z"},
]


def test_rules_are_counted_from_the_clean_eval_reviewer_only():
    threads = host(lambda pr: (VIEW, INLINE))
    d = review_of(threads, 7)
    assert isinstance(d, ReviewSignal)
    assert d.rules_cited == ["CA-01", "R-03"]  # the human's [Q-02] is ignored
    assert d.new_findings == 1
    assert d.clean_eval_marker == {"tier": "High"}  # the human's forged marker doesn't count
    assert d.untrusted_markers_ignored == 1
    assert review_of(threads, 7, reviewer_logins=["human"]).clean_eval_marker == {"tier": "Low"}


def test_a_failed_fetch_is_recorded_with_its_kind():
    def missing(pr):
        raise RemoteError("no PR %d" % pr, "not_found")

    assert review_of(host(missing), 8) == FetchFailure(error="not_found", message="no PR 8")


def test_collecting_with_a_code_host_flags_unreliable_rule_metrics():
    git = MemoryGit()
    git.commit("init", {"src/a/x.py": "1\n"}, when=day("2026-01-01"))
    git.commit("feat: a (#1)", {"src/a/x.py": "2\n"}, when=day("2026-02-01"))
    git.commit("feat: b (#2)", {"src/a/x.py": "3\n"}, when=day("2026-02-02"))

    def rate_limited(pr):
        raise RemoteError("HTTP 403: rate limit exceeded")

    h = collect_history(git, CLOCK, model(), PERIOD, host=host(rate_limited))
    assert sorted(h.rules.prs_fetch_failed) == [1, 2]
    assert not h.rules.reliable
    assert h.rules.prs_reviewed == 0
    assert history.rules_warning(h.rules) == "gh review fetch failed for 2/2 PRs (100%) - rule metrics unreliable"


def test_collecting_with_a_code_host_needs_its_auth():
    with pytest.raises(HeatmapError):
        collect_history(MemoryGit(), CLOCK, model(), PERIOD, host=host(auth_error=HeatmapError("not logged in")))
