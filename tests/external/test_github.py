"""The GitHub gateway (external/github.py), with the `gh` CLI mocked (the `gh` fixture, subprocess.run): the commands
and the stdin it gives gh, what gh prints read into the domain's models and validated, every page of paginated output
read, failures classified, how a review is posted and tried again with less when GitHub refuses it, and the PR a merge's
subject names."""

import json

import pytest
from pytest_lazy_fixtures import (
    lf,
    lfc,
)

from clean_eval.domain.errors import RemoteError
from clean_eval.domain.retro import ReviewThreads
from clean_eval.domain.review import (
    HostReview,
    ReviewPosting,
)
from clean_eval.external.github import (
    PR_FIELDS,
    GitHubGateway,
    loads_multi,
)


class TestPullRequest:
    """`gh pr view`: the PR is read into a PullRequest, validated before it is used, and its head is fetched from the
    PR's own ref."""

    @pytest.mark.parametrize("gh", [[{"out": lf("pull_request")}]], indirect=True)
    def test_a_pull_request_is_read_with_gh_pr_view(
        self,
        gh,
    ):
        pr = GitHubGateway().pull_request(7)
        assert (pr.author, pr.base_ref, pr.head_ref, pr.cross_repository) == ("dev", "main", "feature", False)
        assert (pr.base_sha, pr.head_sha, pr.head_refspec) == ("a" * 40, "b" * 40, "pull/7/head")
        assert gh.call_args.args[0] == ["gh", "pr", "view", "7", "--json", PR_FIELDS]

    @pytest.mark.parametrize(
        "gh", [[{"out": lfc(lambda pr: {**pr, "headRefOid": "--upload-pack=x"}, lf("pull_request"))}]], indirect=True
    )
    def test_a_head_that_is_not_a_commit_id_is_refused(
        self,
        gh,
    ):
        with pytest.raises(RemoteError):  # never handed on to git
            GitHubGateway().pull_request(7)


class TestReviewThreads:
    """What was said on a PR: its reviews and comments, then its inline comments read page by page, each with its
    author's login and whether that is an app's; its named labels and its size. A failure is classified."""

    @pytest.mark.parametrize(
        "gh", [[{"out": lf("review_threads_view")}, {"out": lf("inline_comment_pages")}]], indirect=True
    )
    def test_every_page_of_inline_comments_is_read(
        self,
        gh,
    ):
        assert GitHubGateway().review_threads(7) == ReviewThreads.model_validate(
            {
                "url": "u",
                "labels": ["bug"],
                "size": 2,
                "comments": [
                    {"author": "clean-eval[bot]", "by_app": True, "body": "r"},
                    {"author": "dev", "by_app": False, "body": ""},
                    {"author": "a", "by_app": False, "body": "x"},
                    {"author": "b", "by_app": False, "body": "y"},
                ],
            }
        )
        assert [made.args[0] for made in gh.call_args_list] == [
            ["gh", "pr", "view", "7", "--json", "reviews,comments,labels,additions,deletions,url"],
            ["gh", "api", "--paginate", "repos/{owner}/{repo}/pulls/7/comments"],
        ]

    @pytest.mark.parametrize("gh", [[{"out": []}]], indirect=True)
    def test_a_pull_request_object_is_required(
        self,
        gh,
    ):
        with pytest.raises(RemoteError) as raised:  # a fetch failure of this PR, not a crash of the whole retro
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
    """A review is posted as JSON on gh's stdin, its comments on the diff's right side and its defaults left out; the
    review posted is read back, and a reply that can't be read is still a posted review. When GitHub refuses it, it is
    tried again without the inline comments, then as a comment: each attempt is reported."""

    @pytest.mark.parametrize("gh", [[{"out": {"id": 50, "user": {"login": "github-actions[bot]"}}}]], indirect=True)
    def test_the_review_goes_to_the_pr_as_json_on_stdin(
        self,
        gh,
        review_with_comments,
    ):
        assert GitHubGateway().post_review(1, review_with_comments) == ReviewPosting.model_validate(
            {
                "attempts": [{"tried": "event=REQUEST_CHANGES, inline comments=1"}],
                "review": {"id": 50, "author": "github-actions[bot]"},
            }
        )
        assert gh.call_args.args[0] == [
            "gh",
            "api",
            "-X",
            "POST",
            "repos/{owner}/{repo}/pulls/1/reviews",
            "--input",
            "-",
        ]
        assert json.loads(gh.call_args.kwargs["input"]) == {
            "commit_id": review_with_comments.commit,
            "body": "b",
            "event": "REQUEST_CHANGES",
            "comments": [{"path": "a.py", "line": 2, "side": "RIGHT", "body": "[CA-01][blocker] s"}],
        }

    def test_a_review_without_inline_comments_sends_none(
        self,
        gh,
        review_without_comments,
    ):
        GitHubGateway().post_review(1, review_without_comments)
        sent = json.loads(gh.call_args.kwargs["input"])
        assert sent["event"] == "COMMENT"
        assert "comments" not in sent

    @pytest.mark.parametrize("gh", [[{"out": "<html>oops</html>"}]], indirect=True)
    def test_a_reply_that_cannot_be_read_is_still_a_posted_review(
        self,
        gh,
        review_with_comments,
    ):
        posting = GitHubGateway().post_review(1, review_with_comments)
        assert posting.review == HostReview()  # posted: never to be retried
        gh.assert_called_once()

    @pytest.mark.parametrize(
        "gh", [[{"exit": 1, "err": "HTTP 422: line must be part of the diff"}, {"out": {"id": 51}}]], indirect=True
    )
    def test_a_refused_review_goes_up_without_its_inline_comments(
        self,
        gh,
        review_with_comments,
    ):
        posting = GitHubGateway().post_review(1, review_with_comments)
        assert [attempt.tried for attempt in posting.attempts] == [
            "event=REQUEST_CHANGES, inline comments=1",
            "event=REQUEST_CHANGES, inline comments=0",
        ]
        assert posting.attempts[0].error is not None
        assert "HTTP 422" in posting.attempts[0].error
        assert posting.attempts[1].error is None
        assert posting.review == HostReview(
            id=51,
        )
        sent = [json.loads(made.kwargs["input"]) for made in gh.call_args_list]
        assert [("comments" in post, post["event"]) for post in sent] == [
            (True, "REQUEST_CHANGES"),
            (False, "REQUEST_CHANGES"),
        ]

    @pytest.mark.parametrize("gh", [[{"exit": 1, "err": "HTTP 422: no"}] * 4], indirect=True)
    def test_a_review_refused_every_time_is_not_posted(
        self,
        gh,
        review_with_comments,
    ):
        posting = GitHubGateway().post_review(1, review_with_comments)
        assert posting.review is None
        assert [attempt.tried for attempt in posting.attempts] == [
            "event=REQUEST_CHANGES, inline comments=1",
            "event=REQUEST_CHANGES, inline comments=0",
            "event=COMMENT, inline comments=1",
            "event=COMMENT, inline comments=0",
        ]
        assert gh.call_count == 4

    @pytest.mark.parametrize(
        "gh", [[{"exit": 1, "err": "HTTP 403: Resource not accessible by integration"}]], indirect=True
    )
    def test_a_refusal_for_want_of_permission_names_the_permission(
        self,
        gh,
        review_without_comments,
    ):
        posting = GitHubGateway().post_review(1, review_without_comments)
        assert posting.review is None
        assert len(posting.attempts) == 1  # a comment without inline comments has nothing less to try
        assert posting.attempts[0].error is not None
        assert posting.attempts[0].error.endswith("(the token needs pull-requests: write)")


class TestReviews:
    """A PR's reviews, read from every page into HostReviews, and the dismissal of one."""

    @pytest.mark.parametrize("gh", [[{"out": lf("review_pages")}]], indirect=True)
    def test_every_readable_review_is_listed(
        self,
        gh,
    ):
        assert GitHubGateway().reviews(1) == [
            HostReview(
                id=10,
                author="github-actions[bot]",
                requests_changes=True,
                body="m",
            ),
            HostReview(
                id=11,
            ),
        ]
        assert gh.call_args.args[0] == ["gh", "api", "--paginate", "repos/{owner}/{repo}/pulls/1/reviews"]

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
            "repos/{owner}/{repo}/pulls/1/reviews/10/dismissals",
            "-f",
            "message=Superseded",
            "-f",
            "event=DISMISS",
        ]


class TestMergedPr:
    """The PR a first-parent commit merged, from its subject as GitHub writes it: a merge commit's number, or the last
    `(#n)` of a squash commit; none for a direct push. gh isn't run."""

    @pytest.mark.parametrize(
        ("subject", "number"),
        [
            ("Fix #45 (#67)", 67),
            ("Merge pull request #12 from a/b", 12),
            ("feat: thing (#478)", 478),
            ("chore: no pr", None),
        ],
        ids=["squash that names an issue", "merge commit", "squash", "direct push"],
    )
    def test_the_pr_comes_from_the_subject(
        self,
        gh,
        subject,
        number,
    ):
        assert GitHubGateway().merged_pr(subject) == number
        gh.assert_not_called()


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
