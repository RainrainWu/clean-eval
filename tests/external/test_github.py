"""The GitHub gateway (external/github.py), against a fake `gh` executable: what it reads is validated, every page of
paginated output is read, and failures are classified."""
import os
import unittest
from unittest import mock

from tests.helpers import FakeBin

from clean_eval.domain.errors import RemoteError
from clean_eval.external.github import GitHubGateway, loads_multi


class GitHubGatewayTest(unittest.TestCase):
    def test_github_validates_what_it_reads(self):
        fake = FakeBin()
        try:
            pr = {"number": 7, "title": "t", "baseRefName": "main", "headRefName": "f", "baseRefOid": "a" * 40,
                  "headRefOid": "b" * 40, "author": {"login": "dev"}}
            with mock.patch.dict(os.environ, fake.env()):
                fake.gh({"pr_view": {"out": pr}})
                self.assertEqual(GitHubGateway().pull_request(7).author, "dev")
                fake.gh({"pr_view": {"out": {**pr, "headRefOid": "--upload-pack=x"}}})
                with self.assertRaises(RemoteError):  # never handed on to git
                    GitHubGateway().pull_request(7)
        finally:
            fake.cleanup()


    def test_review_threads_read_every_page_and_classify_failures(self):
        fake = FakeBin()
        try:
            with mock.patch.dict(os.environ, fake.env()):
                fake.gh({"pr_view": {"out": {"url": "u", "reviews": [], "comments": [], "labels": [], "additions": 1,
                                             "deletions": 1}},
                         "inline_comments": {"out": '[{"user":{"login":"a"},"body":"x"}][{"user":{"login":"b"},"body":"y"}]'}})
                view, inline = GitHubGateway().review_threads(7)
                self.assertEqual((view["url"], [c["body"] for c in inline]), ("u", ["x", "y"]))  # both pages
                fake.gh({"pr_view_8": {"exit": 1, "err": "GraphQL: Could not resolve to a PullRequest with the number of 8."},
                         "pr_view": {"exit": 1, "err": "HTTP 502: Bad Gateway"}})
                for number, kind in ((8, "not_found"), (9, "error")):
                    with self.assertRaises(RemoteError) as cm:
                        GitHubGateway().review_threads(number)
                    self.assertEqual(cm.exception.kind, kind)
        finally:
            fake.cleanup()

    def test_paginated_output_is_read_as_one_document(self):
        self.assertEqual(loads_multi('[{"a":1}][{"a":2}]\n[]'), [{"a": 1}, {"a": 2}])
        self.assertEqual(loads_multi('{"x": 1}'), {"x": 1})


if __name__ == "__main__":
    unittest.main()
