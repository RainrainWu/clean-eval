"""The GitHub gateway (external/github.py), with the `gh` CLI mocked (the `gh` fixture, subprocess.run): the commands
and the stdin it gives gh, what it reads validated, every page of paginated output read, and failures classified."""

import json

import pytest

from clean_eval.domain.errors import RemoteError
from clean_eval.domain.review import HostReview, InlineComment, ReviewPost
from clean_eval.external.github import PR_FIELDS, GitHubGateway, loads_multi

PR = {
    "number": 7,
    "title": "t",
    "baseRefName": "main",
    "headRefName": "f",
    "baseRefOid": "a" * 40,
    "headRefOid": "b" * 40,
    "author": {"login": "dev"},
}
# A PR's reviews and comments (`gh pr view --json ...`), and its inline comments in two pages, one JSON document each
# (`gh api --paginate`).
VIEW = {"url": "u", "reviews": [], "comments": [], "labels": [], "additions": 1, "deletions": 1}
PAGES = '[{"user":{"login":"a"},"body":"x"}][{"user":{"login":"b"},"body":"y"}]'
HEAD = "c" * 40
BOT = "github-actions[bot]"
COMMENT = InlineComment(path="a.py", line=2, side="RIGHT", body="[CA-01][blocker] s")
WITH_COMMENTS = ReviewPost(commit_id=HEAD, body="b", event="REQUEST_CHANGES", comments=[COMMENT])
WITHOUT_COMMENTS = ReviewPost(commit_id=HEAD, body="b", event="COMMENT")
# A PR's reviews in two pages, one of them not readable as a review (never one of ours to dismiss).
REVIEW_PAGES = (
    '[{"id": 10, "state": "CHANGES_REQUESTED", "user": {"login": "github-actions[bot]"}, "body": "m"}]'
    '[{"id": "x"}, {"id": 11, "state": "COMMENTED", "user": null, "body": null}]'
)
REVIEWS = "repos/{owner}/{repo}/pulls/1/reviews"


class TestPullRequest:
    """`gh pr view`: the PR is validated before it is used."""

    @pytest.mark.parametrize("gh", [[{"out": PR}]], indirect=True)
    def test_a_pull_request_is_read_with_gh_pr_view(
        self,
        gh,
    ):
        assert GitHubGateway().pull_request(7).author == "dev"
        assert gh.call_args.args[0] == ["gh", "pr", "view", "7", "--json", PR_FIELDS]

    @pytest.mark.parametrize("gh", [[{"out": {**PR, "headRefOid": "--upload-pack=x"}}]], indirect=True)
    def test_a_head_that_is_not_a_commit_id_is_refused(
        self,
        gh,
    ):
        with pytest.raises(RemoteError):  # never handed on to git
            GitHubGateway().pull_request(7)


class TestReviewThreads:
    """A PR's reviews and comments, with its inline comments read page by page; a failure is classified."""

    @pytest.mark.parametrize("gh", [[{"out": VIEW}, {"out": PAGES}]], indirect=True)
    def test_every_page_of_inline_comments_is_read(
        self,
        gh,
    ):
        view, inline = GitHubGateway().review_threads(7)
        assert (view["url"], [comment["body"] for comment in inline]) == ("u", ["x", "y"])
        assert [made.args[0] for made in gh.call_args_list] == [
            ["gh", "pr", "view", "7", "--json", "reviews,comments,labels,additions,deletions,url"],
            ["gh", "api", "--paginate", "repos/{owner}/{repo}/pulls/7/comments"],
        ]

    @pytest.mark.parametrize("gh", [[{"out": []}]], indirect=True)
    def test_a_pull_request_object_is_required(
        self,
        gh,
    ):
        with pytest.raises(RemoteError) as raised:  # a fetch failure of this PR, not a crash of the whole eval
            GitHubGateway().review_threads(7)
        assert raised.value.kind == "error"

    @pytest.mark.parametrize(
        ("number", "gh", "kind"),
        [
            (8, [{"exit": 1, "err": "GraphQL: Could not resolve to a PullRequest with the number of 8."}], "not_found"),
            (9, [{"exit": 1, "err": "HTTP 502: Bad Gateway"}], "error"),
        ],
        indirect=["gh"],
        ids=["no such PR", "anything else"],
    )
    def test_a_failure_is_classified(
        self,
        number,
        gh,
        kind,
    ):
        with pytest.raises(RemoteError) as raised:
            GitHubGateway().review_threads(number)
        assert raised.value.kind == kind


class TestPostReview:
    """A review is posted as JSON on gh's stdin, its defaults left out; the review posted is read back, and a reply that
    can't be read is still a posted review."""

    @pytest.mark.parametrize("gh", [[{"out": {"id": 50, "user": {"login": BOT}}}]], indirect=True)
    def test_the_review_goes_to_the_pr_as_json_on_stdin(
        self,
        gh,
    ):
        assert GitHubGateway().post_review(1, WITH_COMMENTS) == HostReview(id=50, user=BOT)
        assert gh.call_args.args[0] == ["gh", "api", "-X", "POST", REVIEWS, "--input", "-"]
        assert json.loads(gh.call_args.kwargs["input"]) == {
            "commit_id": HEAD,
            "body": "b",
            "event": "REQUEST_CHANGES",
            "comments": [{"path": "a.py", "line": 2, "side": "RIGHT", "body": "[CA-01][blocker] s"}],
        }

    def test_a_review_without_inline_comments_sends_none(
        self,
        gh,
    ):
        GitHubGateway().post_review(1, WITHOUT_COMMENTS)
        assert "comments" not in json.loads(gh.call_args.kwargs["input"])

    @pytest.mark.parametrize("gh", [[{"out": "<html>oops</html>"}]], indirect=True)
    def test_a_reply_that_cannot_be_read_is_still_a_posted_review(
        self,
        gh,
    ):
        assert GitHubGateway().post_review(1, WITH_COMMENTS) == HostReview()  # posted: never to be retried
        gh.assert_called_once()

    @pytest.mark.parametrize("gh", [[{"exit": 1, "err": "HTTP 422: line must be part of the diff"}]], indirect=True)
    def test_a_rejected_review_is_an_error(
        self,
        gh,
    ):
        with pytest.raises(RemoteError) as raised:
            GitHubGateway().post_review(1, WITH_COMMENTS)
        assert raised.value.kind == "error"
        assert "HTTP 422" in str(raised.value)


class TestReviews:
    """A PR's reviews, read from every page, and the dismissal of one."""

    @pytest.mark.parametrize("gh", [[{"out": REVIEW_PAGES}]], indirect=True)
    def test_every_readable_review_is_listed(
        self,
        gh,
    ):
        assert GitHubGateway().reviews(1) == [
            HostReview(id=10, state="CHANGES_REQUESTED", user=BOT, body="m"),
            HostReview(id=11, state="COMMENTED", user=None, body=""),
        ]
        assert gh.call_args.args[0] == ["gh", "api", "--paginate", REVIEWS]

    def test_a_review_is_dismissed_with_a_message(
        self,
        gh,
    ):
        GitHubGateway().dismiss_review(1, 10, "Superseded")
        assert gh.call_args.args[0] == [
            "gh",
            "api",
            "-X",
            "PUT",
            REVIEWS + "/10/dismissals",
            "-f",
            "message=Superseded",
            "-f",
            "event=DISMISS",
        ]


class TestLoadsMulti:
    """gh's output read as one document: `gh api --paginate` prints one JSON document per page."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [('[{"a":1}][{"a":2}]\n[]', [{"a": 1}, {"a": 2}]), ('{"x": 1}', {"x": 1})],
        ids=["pages of a list", "one document"],
    )
    def test_paginated_output_is_read_as_one_document(
        self,
        text,
        expected,
    ):
        assert loads_multi(text) == expected
