"""GitHub, through its CLI (`gh`), for the repository in the current folder: the use cases' CodeHost interface.
Everything about GitHub stays here: gh's commands and the JSON they print (read into the domain's models), how a review
is posted (its event, the diff side of its comments, and what to retry with when GitHub refuses it), the ref a PR's head
is fetched from, the subjects of GitHub's merge and squash commits, and app logins. Failures raise RemoteError:
"not_found" (no such PR) or "error" (anything else: auth, rate limits, the network)."""

import json
import re
import subprocess
from typing import Optional

from pydantic import ValidationError

from clean_eval.domain.errors import (
    HeatmapError,
    RemoteError,
    RemoteFailure,
)
from clean_eval.domain.retro import ReviewThreads
from clean_eval.domain.review import (
    HostReview,
    InlineComment,
    PostAttempt,
    PullRequest,
    ReviewPost,
    ReviewPosting,
)
from clean_eval.use_cases.interfaces import CodeHost

NOT_FOUND = re.compile(r"Could not resolve to a PullRequest|HTTP 404|Not Found", re.I)
FORBIDDEN = re.compile(r"HTTP 403|Resource not accessible by integration", re.I)
PR_FIELDS = "number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,isCrossRepository"
THREAD_FIELDS = "reviews,comments,labels,additions,deletions,url"
MERGE_SUBJECT = re.compile(r"Merge pull request #(\d+)")  # the subject of a merge commit GitHub writes
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)")  # a squash commit's subject ends with "(#123)": the last one is the PR's


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


def listed(
    value: object,
) -> list[object]:
    """`value` when gh printed a list there, else an empty one."""
    return value if isinstance(value, list) else []


def count_of(
    value: object,
) -> int:
    """`value` when gh printed a whole number there, else 0."""
    return value if isinstance(value, int) else 0


def login(
    account: object,
) -> Optional[str]:
    """The login of an account as gh prints it (`{"login": ...}`); a login printed bare as it is; else None."""
    if isinstance(account, dict):
        name = account.get("login")
        return name if isinstance(name, str) else None
    return account if isinstance(account, str) else None


def is_app(
    name: Optional[str],
) -> bool:
    """Whether a login is a GitHub App's: `<name>[bot]`, which no user can register."""
    return name is not None and name.endswith("[bot]")


def pull_request_fields(
    shown: object,
    number: int,
) -> dict[str, object]:
    """A PullRequest's fields, from what `gh pr view --json PR_FIELDS` printed for PR `number`. Its head is fetched
    from the PR's own ref, which GitHub keeps for every PR, a fork's too."""
    pr = shown if isinstance(shown, dict) else {}
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "body": pr.get("body"),
        "author": login(pr.get("author")),
        "url": pr.get("url"),
        "base_ref": pr.get("baseRefName"),
        "head_ref": pr.get("headRefName"),
        "base_sha": pr.get("baseRefOid"),
        "head_sha": pr.get("headRefOid"),
        "cross_repository": pr.get("isCrossRepository", False),
        "head_refspec": "pull/%d/head" % number,
    }


def thread_comments(
    view: dict[str, object],
    inline: object,
) -> list[dict[str, object]]:
    """HostComments' fields of a PR's reviews and comments (in `view`, as `gh pr view` prints them, by `author`), then
    of its inline review comments (`inline`, as the REST API lists them, by `user`), in that order."""
    entries = [(entry, "author") for key in ("reviews", "comments") for entry in listed(view.get(key))]
    entries += [(entry, "user") for entry in listed(inline)]
    comments = []
    for entry, account in entries:
        shown = entry if isinstance(entry, dict) else {}
        name = login(shown.get(account))
        comments.append({"author": name, "by_app": is_app(name), "body": shown.get("body") or ""})
    return comments


def review_threads_of(
    view: dict[str, object],
    inline: object,
) -> dict[str, object]:
    """ReviewThreads' fields, from a PR's `view` and `inline` comments as gh prints them (see thread_comments): its
    size is the lines it adds and removes."""
    labels = [label.get("name") for label in listed(view.get("labels")) if isinstance(label, dict)]
    return {
        "url": view.get("url"),
        "labels": [name for name in labels if isinstance(name, str)],
        "size": count_of(view.get("additions")) + count_of(view.get("deletions")),
        "comments": thread_comments(view, inline),
    }


def host_review(
    shown: object,
) -> Optional[HostReview]:
    """A review as GitHub's API shows it, as a HostReview; None when it can't be read as one."""
    if not isinstance(shown, dict):
        return None
    try:
        return HostReview.model_validate(
            {
                "id": shown.get("id"),
                "author": login(shown.get("user")),
                "requests_changes": shown.get("state") == "CHANGES_REQUESTED",
                "body": shown.get("body") or "",
            }
        )
    except ValidationError:
        return None


def review_attempts(
    post: ReviewPost,
) -> list[tuple[str, list[InlineComment]]]:
    """(event, inline comments) to post `post` with, in turn: GitHub refuses a whole review when one of its inline
    comments is on a line outside the diff (HTTP 422), and a reviewer can't request changes on its own PR."""
    event = "REQUEST_CHANGES" if post.request_changes else "COMMENT"
    attempts = []
    for attempt in ((event, post.comments), (event, []), ("COMMENT", post.comments), ("COMMENT", [])):
        if attempt not in attempts:
            attempts.append(attempt)
    return attempts


def review_payload(
    post: ReviewPost,
    event: str,
    comments: list[InlineComment],
) -> str:
    """The JSON GitHub's API takes for a review on `post`'s commit, with its body, `event` and `comments`, each on the
    right side of the diff (the file as the PR leaves it); without a `comments` key when there are none."""
    payload: dict[str, object] = {"commit_id": post.commit, "body": post.body, "event": event}
    if comments:
        payload["comments"] = [
            {"path": comment.path, "line": comment.line, "side": "RIGHT", "body": comment.body} for comment in comments
        ]
    return json.dumps(payload)


def refusal(
    error: RemoteError,
) -> str:
    """Why GitHub refused a review: gh's error, and when it is a permission's, the permission the token needs."""
    text = str(error)
    return text + " (the token needs pull-requests: write)" if FORBIDDEN.search(text) else text


def posted_review(
    reply: str,
) -> HostReview:
    """The review GitHub's reply to a post shows; an empty HostReview when the reply can't be read, since the review
    went up all the same (and is never posted again)."""
    try:
        shown = json.loads(reply)
    except ValueError:
        return HostReview()
    return host_review(shown) or HostReview()


class GitHubGateway(CodeHost):
    def pull_request(
        self,
        number: int,
    ) -> PullRequest:
        shown = gh_json("pr", "view", str(number), "--json", PR_FIELDS)
        try:
            return PullRequest.model_validate(pull_request_fields(shown, number))
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
    ) -> ReviewThreads:
        view = gh_json("pr", "view", str(number), "--json", THREAD_FIELDS)
        if not isinstance(view, dict):
            raise RemoteError(
                "gh pr view %s returned no pull request object" % number,
            )
        inline = gh_json("api", "--paginate", "repos/{owner}/{repo}/pulls/%s/comments" % number)
        try:
            return ReviewThreads.model_validate(review_threads_of(view, inline))
        except ValidationError as e:
            raise RemoteError.from_validation(e, "gh pr view %s returned unexpected review threads" % number)

    def post_review(
        self,
        number: int,
        post: ReviewPost,
    ) -> ReviewPosting:
        attempts = []
        for event, comments in review_attempts(post):
            tried = "event=%s, inline comments=%d" % (event, len(comments))
            try:
                reply = run_gh(
                    "api",
                    "-X",
                    "POST",
                    reviews_path(number),
                    "--input",
                    "-",
                    stdin=review_payload(post, event, comments),
                )
            except RemoteError as error:
                attempts.append(
                    PostAttempt(
                        tried=tried,
                        error=refusal(error),
                    )
                )
                continue
            attempts.append(
                PostAttempt(
                    tried=tried,
                )
            )
            return ReviewPosting(
                attempts=attempts,
                review=posted_review(reply),
            )
        return ReviewPosting(
            attempts=attempts,
        )

    def reviews(
        self,
        number: int,
    ) -> list[HostReview]:
        found = gh_json("api", "--paginate", reviews_path(number))
        # an entry not readable as a review is never one of ours to dismiss
        return [review for review in map(host_review, found if isinstance(found, list) else ()) if review is not None]

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

    def merged_pr(
        self,
        subject: str,
    ) -> Optional[int]:
        merge = MERGE_SUBJECT.search(subject)
        if merge:
            return int(merge.group(1))
        squashed = SQUASH_SUBJECT.findall(subject)
        return int(squashed[-1]) if squashed else None
