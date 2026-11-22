"""The GitHub CLI (`gh`): every call clean-eval makes to GitHub, for the repository in the current folder. It is the use
cases' CodeHost interface: what it reads is validated into domain models. Failures raise RemoteError, classified as
"not_found" (no such PR) or "error" (anything else, e.g. auth, rate limits, the network)."""

import json
import re
import subprocess
from typing import (
    Any,
    Optional,
)

from pydantic import ValidationError

from clean_eval.domain.errors import (
    HeatmapError,
    RemoteError,
    RemoteFailure,
)
from clean_eval.domain.review import (
    HostReview,
    PullRequest,
    ReviewPost,
)
from clean_eval.use_cases.interfaces import CodeHost

NOT_FOUND = re.compile(r"Could not resolve to a PullRequest|HTTP 404|Not Found", re.I)
PR_FIELDS = "number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,isCrossRepository"


def loads_multi(
    text: str,
) -> object:
    """Parse gh output; `gh api --paginate` concatenates one JSON document per page."""
    dec, i, docs, text = json.JSONDecoder(), 0, [], text.strip()
    while i < len(text):
        doc, i = dec.raw_decode(text, i)
        docs.append(doc)
        while i < len(text) and text[i].isspace():
            i += 1
    if len(docs) == 1:
        return docs[0]
    if all(isinstance(d, list) for d in docs):
        return [x for d in docs for x in d]
    return docs


def run_gh(
    *args: str,
    stdin: Optional[str] = None,
) -> str:
    """The output of `gh <args>`, given `stdin`; RemoteError, classified, when it fails."""
    completed = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout).strip()[:300]
        raise RemoteError(
            "gh %s failed: %s" % (" ".join(args[:2]), err),
            kind=RemoteFailure.NOT_FOUND if NOT_FOUND.search(err) else RemoteFailure.ERROR,
        )
    return completed.stdout


def gh_json(
    *args: str,
    stdin: Optional[str] = None,
) -> object:
    """The parsed JSON output of `gh <args>` (every page of a paginated call); RemoteError when it isn't JSON."""
    out = run_gh(*args, stdin=stdin)
    try:
        return loads_multi(out)
    except ValueError as e:
        raise RemoteError(
            "unparseable output of gh %s: %s" % (" ".join(args[:2]), e),
        )


def reviews_path(
    number: int,
) -> str:
    """The API path of pull request `number`'s reviews."""
    return "repos/{owner}/{repo}/pulls/%s/reviews" % number


class GitHubGateway(CodeHost):
    def pull_request(
        self,
        number: int,
    ) -> PullRequest:
        try:
            return PullRequest.model_validate(gh_json("pr", "view", str(number), "--json", PR_FIELDS))
        except ValidationError as e:
            raise RemoteError.from_validation(e, "gh pr view %s returned an unexpected pull request" % number)

    def require_auth(
        self,
    ) -> None:
        try:
            run_gh("auth", "status")
        except RemoteError as e:
            raise HeatmapError(
                "--with-gh needs an authenticated gh CLI: %s" % e,
            )

    def review_threads(
        self,
        number: int,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        view = gh_json("pr", "view", str(number), "--json", "reviews,comments,labels,additions,deletions,url")
        if not isinstance(view, dict):
            raise RemoteError(
                "gh pr view %s returned no pull request object" % number,
            )
        inline = gh_json("api", "--paginate", "repos/{owner}/{repo}/pulls/%s/comments" % number)
        return view, inline if isinstance(inline, list) else []

    def post_review(
        self,
        number: int,
        post: ReviewPost,
    ) -> HostReview:
        out = run_gh(
            "api",
            "-X",
            "POST",
            reviews_path(number),
            "--input",
            "-",
            stdin=post.model_dump_json(exclude_defaults=True),
        )
        try:  # posted: never retried, even when the reply can't be read
            return HostReview.model_validate_json(out)
        except ValidationError:
            return HostReview()

    def reviews(
        self,
        number: int,
    ) -> list[HostReview]:
        found = gh_json("api", "--paginate", reviews_path(number))
        out = []
        for r in found if isinstance(found, list) else ():
            try:
                out.append(HostReview.model_validate(r))
            except ValidationError:
                continue  # not a review we can read: never one of ours to dismiss
        return out

    def dismiss_review(
        self,
        number: int,
        review_id: int,
        message: str,
    ) -> None:
        run_gh(
            "api",
            "-X",
            "PUT",
            "%s/%s/dismissals" % (reviews_path(number), review_id),
            "-f",
            "message=" + message,
            "-f",
            "event=DISMISS",
        )
