"""The PR review CI trusts. The agent only proposes findings: the domain models check their shape, and the rules here
hold them to the rules table (an unknown rule ID becomes NEW, a severity below its rule's is raised to it) and redact
their text; the verdict, the blocker count and the band are derived, never taken from agent text."""
import hashlib
import re

from ...domain.ci import StepOutputs
from ...domain.review import CheckedRules, Finding, InlineComment, Review, ReviewRule, RulesTable
from ...domain.values import SEVERITIES
from .redaction import MARKER_RE, neutralize_comments

RULE_ROW = re.compile(r"^\|\s*([A-Z]{1,4}-\d{2,3})\s*\|\s*(blocker|major|minor)\s*\|\s*(Low|Moderate|Medium|High|Critical)\s*\|",
                      re.M)
# The marker that ends every posted review; the eval reads it back to measure rule usefulness.
REVIEW_MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")


def _rows(text):
    return [ReviewRule(id=m.group(1), severity=m.group(2), min_tier=m.group(3)) for m in RULE_ROW.finditer(text)]


def parse_rules(text):
    """The RulesTable of a rules file (rows of its Markdown tables)."""
    return RulesTable(rules=_rows(text))


def check_rules(text):
    """The rules of a rules file an unattended eval may keep; a ValidationError when it has no rows or repeats an ID."""
    return CheckedRules(rules=_rows(text))


def rules_version(text):
    """Short id of a rules file: its git blob hash (as `git hash-object` gives it), 8 characters."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:8]


def finalize_review(agent, risk, rules, redactor):
    """The Review CI trusts, from the agent's validated AgentReview, the RiskReport of the change and the base's rules."""
    known, findings, redactions = rules.by_id, [], 0
    for f in agent.findings:
        rule, severity, notes = f.rule, f.severity, []
        if rule != "NEW" and rule not in known:
            notes.append("cited unknown rule %s" % rule)
            rule = "NEW"
        if rule in known and SEVERITIES.index(severity) < SEVERITIES.index(known[rule].severity):
            notes.append("severity raised from %s to the rule's %s" % (severity, known[rule].severity))
            severity = known[rule].severity
        summary, n1 = redactor.redact(neutralize_comments(f.summary))
        fix, n2 = redactor.redact(neutralize_comments(f.fix))
        redactions += n1 + n2
        findings.append(Finding(rule=rule, severity=severity, file=f.file, line=f.line, summary=summary, fix=fix, notes=notes))
    return Review(pr=risk.pr, base=risk.base, head=risk.head, max_tier=risk.max_tier, max_score=risk.max_score,
                  rules_version=risk.rules_version, model_version=risk.model_version, findings=findings, redactions=redactions)


def review_outputs(review):
    return StepOutputs({"verdict": review.verdict, "max-tier": review.max_tier, "max-score": str(review.max_score),
                        "blockers": str(review.blockers)})


def marker(review):
    rules = sorted({f.rule for f in review.findings} - {"NEW"})
    return "<!-- clean-eval:pr-review tier=%s score=%s rules=%s rules_version=%s -->" % (
        review.max_tier, review.max_score, ",".join(rules), review.rules_version or "")


def compose_review(review, report, on_diff=None):
    """(body, event, inline comments) of the review to post. Only findings on lines in the diff (`on_diff`: {path: lines},
    None when unknown) are attached inline, since GitHub rejects the whole review otherwise; the rest are listed in the
    body, which ends with the clean-eval marker."""
    comments, detached = [], []
    for f in review.findings:
        if not (f.file and f.line):
            continue
        if on_diff is None or f.line in on_diff.get(f.file, ()):
            comments.append(InlineComment(path=f.file, line=f.line, side="RIGHT",
                                          body="[%s][%s] %s" % (f.rule, f.severity, f.summary)
                                               + ("\n\n**Fix:** %s" % f.fix if f.fix else "")))
        else:
            detached.append("- [%s][%s] `%s:%s`: %s" % (f.rule, f.severity, f.file, f.line, f.summary))
    extra = ("\n\n**Not attached inline** (line is outside the diff):\n" + "\n".join(detached)) if detached else ""
    body = "%s%s\n\n%s" % (report.strip(), extra, marker(review))
    event = "REQUEST_CHANGES" if review.verdict == "request_changes" else "COMMENT"
    return body, event, comments


def post_attempts(event, comments):
    """(event, comments) to try in turn: GitHub rejects inline comments on lines outside the diff (422), and a reviewer
    can't request changes on its own PR."""
    attempts = []
    for attempt in ((event, comments), (event, []), ("COMMENT", comments), ("COMMENT", [])):
        if attempt not in attempts:
            attempts.append(attempt)
    return attempts


def stale_reviews(reviews, me, my_id):
    """IDs of this reviewer's earlier "changes requested" clean-eval reviews (HostReviews), which `my_id` supersedes."""
    if not me:
        return []
    return [r.id for r in reviews if r.id not in (None, my_id) and r.state == "CHANGES_REQUESTED" and r.user == me
            and MARKER_RE.search(r.body)]
