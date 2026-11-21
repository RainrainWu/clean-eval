"""Use case: post the validated review on its PR as one review, with the findings on diff lines attached inline, then
dismiss this reviewer's earlier "changes requested" reviews, so a fixed PR isn't left blocked."""
from dataclasses import dataclass, field

from ..domain.errors import RemoteError
from ..domain.review import DiffIndex, Review, ReviewPost
from .rules.diff import commentable_lines
from .rules.review import compose_review, post_attempts, stale_reviews


@dataclass
class PostOutcome:
    posted: bool = False
    log: list = field(default_factory=list)  # (level, message), level "info" or "warning"


def lines_on_diff(ctx):
    """{path: new-side lines GitHub accepts inline comments on}, from the precomputed per-file patches; None without them."""
    if ctx is None or not ctx.exists("diff/index.json"):
        return None
    index = DiffIndex.model_validate(ctx.read_json("diff/index.json")).root
    return {e.file: commentable_lines(ctx.read_text(e.patch)) for e in index}


class PostReview:
    def __init__(self, host, out, ctx, redactor):
        self.host, self.out, self.ctx, self.redactor = host, out, ctx, redactor

    def execute(self, pr, head):
        outcome = PostOutcome()
        review = Review.model_validate(self.out.read_json("result.json"))
        body, event, comments = compose_review(review, self.out.read_text("report.md"), lines_on_diff(self.ctx))
        posted = None
        for ev, cm in post_attempts(event, comments):
            try:
                posted = self.host.post_review(pr, ReviewPost(commit_id=head, body=body, event=ev, comments=cm))
            except RemoteError as e:
                outcome.log.append(("warning", "review post failed (event=%s, comments=%d): %s"
                                    % (ev, len(cm), self._error(e, 300))))
                continue
            outcome.posted = True
            outcome.log.append(("info", "posted review on #%s: event=%s, inline comments=%d" % (pr, ev, len(cm))))
            break
        if not outcome.posted:
            return outcome

        try:
            reviews = self.host.reviews(pr)
        except RemoteError as e:
            outcome.log.append(("warning", "could not list reviews to dismiss stale ones: %s" % self._error(e, 200)))
            return outcome
        for review_id in stale_reviews(reviews, posted.user, posted.id):
            try:
                self.host.dismiss_review(pr, review_id, "Superseded by the clean-eval review of %s" % head[:8])
                outcome.log.append(("info", "dismissed stale review %s" % review_id))
            except RemoteError as e:
                outcome.log.append(("warning", "could not dismiss review %s: %s" % (review_id, self._error(e, 200))))
        return outcome

    def _error(self, e, limit):
        return self.redactor.redact(str(e))[0][:limit]  # redact before cutting: host errors can echo request data
