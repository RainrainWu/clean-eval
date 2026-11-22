"""The GitHub gateway (external/github.py), against a fake `gh` executable: what it reads is validated, every page of
paginated output is read, and failures are classified."""

import pytest

from clean_eval.domain.errors import RemoteError
from clean_eval.external.github import GitHubGateway, loads_multi

PR = {
    "number": 7,
    "title": "t",
    "baseRefName": "main",
    "headRefName": "f",
    "baseRefOid": "a" * 40,
    "headRefOid": "b" * 40,
    "author": {"login": "dev"},
}


def test_a_pull_request_is_validated_before_use(fake_bin):
    fake_bin.gh({"pr_view": {"out": PR}})
    assert GitHubGateway().pull_request(7).author == "dev"
    fake_bin.gh({"pr_view": {"out": {**PR, "headRefOid": "--upload-pack=x"}}})
    with pytest.raises(RemoteError):  # never handed on to git
        GitHubGateway().pull_request(7)


def test_review_threads_read_every_page(fake_bin):
    view = {"url": "u", "reviews": [], "comments": [], "labels": [], "additions": 1, "deletions": 1}
    pages = '[{"user":{"login":"a"},"body":"x"}][{"user":{"login":"b"},"body":"y"}]'
    fake_bin.gh({"pr_view": {"out": view}, "inline_comments": {"out": pages}})
    view, inline = GitHubGateway().review_threads(7)
    assert (view["url"], [c["body"] for c in inline]) == ("u", ["x", "y"])


def test_review_threads_need_a_pull_request_object(fake_bin):
    fake_bin.gh({"pr_view": {"out": []}})
    with pytest.raises(RemoteError) as raised:  # a fetch failure of this PR, not a crash of the whole eval
        GitHubGateway().review_threads(7)
    assert raised.value.kind == "error"


@pytest.mark.parametrize(("number", "kind"), [(8, "not_found"), (9, "error")], ids=["no such PR", "anything else"])
def test_review_thread_failures_are_classified(fake_bin, number, kind):
    fake_bin.gh(
        {
            "pr_view_8": {"exit": 1, "err": "GraphQL: Could not resolve to a PullRequest with the number of 8."},
            "pr_view": {"exit": 1, "err": "HTTP 502: Bad Gateway"},
        }
    )
    with pytest.raises(RemoteError) as raised:
        GitHubGateway().review_threads(number)
    assert raised.value.kind == kind


def test_paginated_output_is_read_as_one_document():
    assert loads_multi('[{"a":1}][{"a":2}]\n[]') == [{"a": 1}, {"a": 2}]
    assert loads_multi('{"x": 1}') == {"x": 1}
