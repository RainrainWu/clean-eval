"""Use case: post the validated review on its PR as one review, with the findings on diff lines attached inline, then
dismiss this reviewer's earlier "changes requested" reviews, so a fixed PR isn't left blocked. Through the CodeHost,
FileSystem and Environment interfaces (the environment names the secrets to redact from host errors)."""
from clean_eval.domain.errors import RemoteError
from clean_eval.domain.review import DiffIndex, PostOutcome, Review, ReviewPost
from clean_eval.use_cases.rules.diff import commentable_lines
from clean_eval.use_cases.rules.redaction import redact, secret_values
from clean_eval.use_cases.rules.review import compose_review, post_attempts, stale_reviews
from clean_eval.use_cases.store import read_json


def lines_on_diff(fs, ctx):
    """{path: new-side lines GitHub accepts inline comments on}, from the per-file patches in context folder `ctx`; None
    without them."""
    if ctx is None or not fs.exists(fs.join(ctx, "diff", "index.json")):
        return None
    index = DiffIndex.model_validate(read_json(fs, fs.join(ctx, "diff", "index.json"))).root
    return {e.file: commentable_lines(fs.read_text(fs.join(ctx, e.patch))) for e in index}


def post_review(host, fs, env, out, pr, head, ctx=None):
    """Post out/result.json and out/report.md as the review of PR `pr` at commit `head`; the PostOutcome."""
    secrets = secret_values(env.variables())

    def error(e, limit):
        return redact(str(e), secrets)[0][:limit]  # redact before cutting: host errors can echo request data

    outcome = PostOutcome()
    review = Review.model_validate(read_json(fs, fs.join(out, "result.json")))
    body, event, comments = compose_review(review, fs.read_text(fs.join(out, "report.md")), lines_on_diff(fs, ctx))
    posted = None
    for ev, cm in post_attempts(event, comments):
        try:
            posted = host.post_review(pr, ReviewPost(commit_id=head, body=body, event=ev, comments=cm))
        except RemoteError as e:
            outcome.log.append(("warning", "review post failed (event=%s, comments=%d): %s" % (ev, len(cm), error(e, 300))))
            continue
        outcome.posted = True
        outcome.log.append(("info", "posted review on #%s: event=%s, inline comments=%d" % (pr, ev, len(cm))))
        break
    if not outcome.posted:
        return outcome

    try:
        reviews = host.reviews(pr)
    except RemoteError as e:
        outcome.log.append(("warning", "could not list reviews to dismiss stale ones: %s" % error(e, 200)))
        return outcome
    for review_id in stale_reviews(reviews, posted.user, posted.id):
        try:
            host.dismiss_review(pr, review_id, "Superseded by the clean-eval review of %s" % head[:8])
            outcome.log.append(("info", "dismissed stale review %s" % review_id))
        except RemoteError as e:
            outcome.log.append(("warning", "could not dismiss review %s: %s" % (review_id, error(e, 200))))
    return outcome
