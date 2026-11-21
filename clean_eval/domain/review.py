"""The PR review CI trusts. The agent only proposes findings: their rule IDs, severities and lines are checked here, and
the verdict, the blocker count and the risk band are derived, never taken from agent text."""
import hashlib
import re

from .errors import InvalidOutput
from .policy import BAND_NAMES
from .redaction import MARKER_RE, neutralize_comments

RULE_RE = re.compile(r"^([A-Z]{1,4}-\d{2,3}|NEW)$")
RULE_ROW = re.compile(r"^\|\s*([A-Z]{1,4}-\d{2,3})\s*\|\s*(blocker|major|minor)\s*\|\s*(Low|Moderate|Medium|High|Critical)\s*\|",
                      re.M)
SEVERITIES = ("minor", "major", "blocker")  # ascending
LEGACY_BANDS = {"Medium": "Moderate"}  # "Medium" is the pre-v2 name of the Moderate band
MAX_FINDINGS = 50
# The marker that ends every posted review; the eval reads it back to measure rule usefulness.
REVIEW_MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")


def parse_rules(text):
    """{rule ID: {"severity", "min_tier"}} from the rules tables. Rules files copied from the pre-v2 default keep working."""
    return {m.group(1): {"severity": m.group(2), "min_tier": LEGACY_BANDS.get(m.group(3), m.group(3))}
            for m in RULE_ROW.finditer(text)}


def check_rules(text):
    """The rules of a rules file; InvalidOutput when it has no rule rows or repeats an ID."""
    rules = parse_rules(text)
    if not rules:
        raise InvalidOutput("review-rules.md has no rule rows")
    if len(RULE_ROW.findall(text)) != len(rules):
        raise InvalidOutput("review-rules.md has duplicate rule IDs")
    return rules


def rules_version(text):
    """Short id of a rules file: its git blob hash (as `git hash-object` gives it), 8 characters."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:8]


def text_field(value, field, maxlen, one_line=False):
    """`value` checked to be a string (of one line when asked), cut to `maxlen`."""
    if not isinstance(value, str):
        raise InvalidOutput("%s must be a string" % field)
    if one_line and ("\n" in value or "\r" in value):
        raise InvalidOutput("%s must be a single line" % field)
    return value[:maxlen]


def finalize_review(raw, risk, rules, redactor):
    """The review CI trusts, from the agent's result.json (`raw`), the precomputed risk of the change and the rules of the
    base commit. An unknown rule ID becomes NEW; a severity below its rule's is raised to it; text is redacted."""
    if not isinstance(raw, dict):
        raise InvalidOutput("result.json must be an object")
    items = raw.get("findings") or []
    if not isinstance(items, list):
        raise InvalidOutput("findings must be a list")
    findings, redactions = [], 0
    for i, f in enumerate(items[:MAX_FINDINGS]):
        if not isinstance(f, dict):
            raise InvalidOutput("findings[%d] must be an object" % i)
        rule = text_field(f.get("rule", ""), "findings[%d].rule" % i, 20, one_line=True)
        if not RULE_RE.match(rule):
            raise InvalidOutput("findings[%d].rule %r is not a rule ID or NEW" % (i, rule))
        sev = f.get("severity")
        if sev not in SEVERITIES:
            raise InvalidOutput("findings[%d].severity must be one of %s" % (i, SEVERITIES,))
        line = f.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise InvalidOutput("findings[%d].line must be a positive integer or null" % i)
        file_ = f.get("file")
        file_ = text_field(file_, "findings[%d].file" % i, 500, one_line=True) if file_ else None
        summary = text_field(f.get("summary", ""), "findings[%d].summary" % i, 1500)
        fix = text_field(f.get("fix") or "", "findings[%d].fix" % i, 2500)
        notes = []
        if rule != "NEW" and rule not in rules:
            notes.append("cited unknown rule %s" % rule)
            rule = "NEW"
        if rule in rules and SEVERITIES.index(sev) < SEVERITIES.index(rules[rule]["severity"]):
            notes.append("severity raised from %s to the rule's %s" % (sev, rules[rule]["severity"]))
            sev = rules[rule]["severity"]
        summary, n1 = redactor.redact(neutralize_comments(summary))
        fix, n2 = redactor.redact(neutralize_comments(fix))
        redactions += n1 + n2
        findings.append({"rule": rule, "severity": sev, "file": file_, "line": line, "summary": summary, "fix": fix,
                         "notes": notes})
    blockers = sum(f["severity"] == "blocker" for f in findings)
    majors = sum(f["severity"] == "major" for f in findings)
    tier = risk.get("max_tier")
    if tier not in BAND_NAMES:
        raise InvalidOutput("risk.json has no valid max_tier")
    return {
        "pr": risk.get("pr"), "base": risk.get("base"), "head": risk.get("head"),
        "verdict": "request_changes" if blockers else ("comment" if majors else "approve"),
        "max_tier": tier, "max_score": int(round(float(risk.get("max_score", 0)))),
        "blockers": blockers, "majors": majors,
        "rules_version": risk.get("rules_version"), "model_version": risk.get("model_version"),
        "findings": findings, "redactions": redactions,
    }


def review_outputs(review):
    """The step outputs of a review: enums and numbers only, so no agent text reaches them."""
    return {"verdict": review["verdict"], "max-tier": review["max_tier"], "max-score": str(review["max_score"]),
            "blockers": str(review["blockers"])}


def marker(review):
    rules = sorted({f["rule"] for f in review["findings"]} - {"NEW"})
    return "<!-- clean-eval:pr-review tier=%s score=%s rules=%s rules_version=%s -->" % (
        review["max_tier"], review["max_score"], ",".join(rules), review.get("rules_version") or "")


def compose_review(review, report, on_diff=None):
    """(body, event, inline comments) of the review to post. Only findings on lines in the diff (`on_diff`: {path: lines},
    None when unknown) are attached inline, since GitHub rejects the whole review otherwise; the rest are listed in the
    body, which ends with the clean-eval marker."""
    comments, detached = [], []
    for f in review["findings"]:
        if not (f["file"] and f["line"]):
            continue
        if on_diff is None or f["line"] in on_diff.get(f["file"], ()):
            comments.append({"path": f["file"], "line": f["line"], "side": "RIGHT",
                             "body": "[%s][%s] %s" % (f["rule"], f["severity"], f["summary"])
                                     + ("\n\n**Fix:** %s" % f["fix"] if f["fix"] else "")})
        else:
            detached.append("- [%s][%s] `%s:%s`: %s" % (f["rule"], f["severity"], f["file"], f["line"], f["summary"]))
    extra = ("\n\n**Not attached inline** (line is outside the diff):\n" + "\n".join(detached)) if detached else ""
    body = "%s%s\n\n%s" % (report.strip(), extra, marker(review))
    event = "REQUEST_CHANGES" if review["verdict"] == "request_changes" else "COMMENT"
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
    """IDs of this reviewer's earlier "changes requested" clean-eval reviews, which the review `my_id` supersedes."""
    if not me:
        return []
    return [r["id"] for r in reviews
            if isinstance(r, dict) and r.get("id") not in (None, my_id) and r.get("state") == "CHANGES_REQUESTED"
            and (r.get("user") or {}).get("login") == me and MARKER_RE.search(r.get("body") or "")]
