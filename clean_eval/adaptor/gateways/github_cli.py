"""The GitHub CLI (`gh`): the CodeHost of the repository in the current folder. Failures raise RemoteError, classified as
"not_found" (no such PR) or "error" (anything else, e.g. auth, rate limits, the network)."""
import json
import re
import subprocess

from pydantic import ValidationError

from ...domain.errors import HeatmapError, RemoteError
from ...domain.review import HostReview, PullRequest

NOT_FOUND = re.compile(r"Could not resolve to a PullRequest|HTTP 404|Not Found", re.I)
PR_FIELDS = "number,title,body,author,url,baseRefName,headRefName,baseRefOid,headRefOid,isCrossRepository"


def loads_multi(text):
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


class GitHubCli:
    def _run(self, *args, stdin=None):
        r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True)
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip()[:300]
            raise RemoteError("gh %s failed: %s" % (" ".join(args[:2]), err), kind="not_found" if NOT_FOUND.search(err) else "error")
        return r.stdout

    def _json(self, *args, stdin=None):
        out = self._run(*args, stdin=stdin)
        try:
            return loads_multi(out)
        except ValueError as e:
            raise RemoteError("unparseable output of gh %s: %s" % (" ".join(args[:2]), e))

    @staticmethod
    def _reviews(number):
        return "repos/{owner}/{repo}/pulls/%s/reviews" % number

    def pull_request(self, number):
        try:
            return PullRequest.model_validate(self._json("pr", "view", str(number), "--json", PR_FIELDS))
        except ValidationError as e:
            raise RemoteError.from_validation(e, "gh pr view %s returned an unexpected pull request" % number)

    def require_auth(self):
        try:
            self._run("auth", "status")
        except RemoteError as e:
            raise HeatmapError("--with-gh needs an authenticated gh CLI: %s" % e)

    def review_threads(self, number):
        view = self._json("pr", "view", str(number), "--json", "reviews,comments,labels,additions,deletions,url")
        inline = self._json("api", "--paginate", "repos/{owner}/{repo}/pulls/%s/comments" % number)
        return view, inline if isinstance(inline, list) else []

    def post_review(self, number, post):
        out = self._run("api", "-X", "POST", self._reviews(number), "--input", "-",
                        stdin=post.model_dump_json(exclude_defaults=True))
        try:  # posted: never retried, even when the reply can't be read
            return HostReview.model_validate_json(out)
        except ValidationError:
            return HostReview()

    def reviews(self, number):
        found = self._json("api", "--paginate", self._reviews(number))
        out = []
        for r in found if isinstance(found, list) else ():
            try:
                out.append(HostReview.model_validate(r))
            except ValidationError:
                continue  # not a review we can read: never one of ours to dismiss
        return out

    def dismiss_review(self, number, review_id, message):
        self._run("api", "-X", "PUT", "%s/%s/dismissals" % (self._reviews(number), review_id),
                  "-f", "message=" + message, "-f", "event=DISMISS")
