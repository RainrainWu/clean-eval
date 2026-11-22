"""The PR review's rules: diffs split per file, redaction of what leaves the runner, a change's risk from the heat
map of its base, and the review CI derives from what the agent proposed."""

import base64
import hashlib
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Optional

from clean_eval.domain.policy import SEVERITIES, Policy
from clean_eval.domain.rating import ChangeRisk, Heatmap, ModuleRisk
from clean_eval.domain.review import (
    AgentReview,
    CheckedRules,
    Finding,
    HostReview,
    InlineComment,
    Review,
    ReviewEvent,
    ReviewRule,
    RiskReport,
    RulesTable,
    StepOutputs,
)
from clean_eval.use_cases.rules.analysis import ignored, module_of
from clean_eval.use_cases.rules.heatmap import band, band_floor, severity

# Unified diffs as text: one patch per file, size limits, and the lines GitHub accepts inline comments on.

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def split_patch(
    full: str,
) -> list[str]:
    """Split a `git diff` into one chunk per file (each starts with 'diff --git ')."""
    chunks, cur = [], []
    for line in full.splitlines(keepends=True):
        if line.startswith("diff --git ") and cur:
            chunks.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        chunks.append("".join(cur))
    return chunks


def truncate(
    text: str,
    limit: int,
) -> tuple[str, bool]:
    """(text cut to `limit` characters with a note saying how much is missing, whether it was cut)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[... truncated: %d more characters ...]\n" % (len(text) - limit), True


def line_counts(
    patch: str,
) -> tuple[int, int]:
    """(lines added, lines removed) by a patch."""
    lines = patch.splitlines()
    return (
        sum(1 for line in lines if line.startswith("+") and not line.startswith("+++")),
        sum(1 for line in lines if line.startswith("-") and not line.startswith("---")),
    )


def commentable_lines(
    patch: str,
) -> set[int]:
    """The new-side line numbers of one file's patch that GitHub accepts inline comments on: added and context lines."""
    lines, new = set(), None
    for line in patch.splitlines():
        m = HUNK.match(line)
        if m:
            new = int(m.group(1))
        elif new is None or line.startswith(("-", "\\")):
            continue  # file header, removed line, or "\ No newline at end of file"
        else:  # added ("+") or context (" ") line on the new side
            lines.add(new)
            new += 1
    return lines


# What may leave the CI runner. Agent text and tool output can carry secrets (read from the environment or the checkout)
# and hidden instructions, so everything posted or written out is cleaned first: values of secret-looking environment
# variables and their base64 forms, well-known token formats and authorization headers become [REDACTED], and HTML
# comments are defanged, so nothing hidden or marker-like reaches GitHub.

REDACTED = "[REDACTED]"
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.I)
TOKEN_PATTERNS = [
    re.compile(p)
    for p in (
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{40,}",
        r"sk-ant-[A-Za-z0-9_\-]{20,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"xox[abpr]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    )
]
AUTH_HEADER = re.compile(r"(?i)(authorization:\s*(?:basic|bearer|token)\s+)[A-Za-z0-9+/=._\-]{8,}")
MARKER_RE = re.compile(r"<!--\s*clean-eval:[^>]*-->")
MAX_REPORT_CHARS = 60_000  # GitHub caps review bodies at 65,536


def secret_values(
    environ: Mapping[str, str],
) -> tuple[str, ...]:
    """The values to redact, from an environment mapping: every secret-looking variable's value (8+ characters) and its
    base64 forms, e.g. the `AUTHORIZATION: basic <b64("x-access-token:<token>")>` actions/checkout persists in .git/config.
    Longest first, so a secret that contains another is replaced whole."""
    values = {v for k, v in environ.items() if SECRET_NAME.search(k) and v and len(v) >= 8}
    encoded = {base64.b64encode(raw.encode()).decode() for v in values for raw in (v, "x-access-token:" + v)}
    return tuple(sorted(values | encoded, key=len, reverse=True))


def neutralize_comments(
    text: str,
) -> str:
    """Agent text may not carry HTML comments: strip clean-eval markers until none reassemble from nested input, then
    defang every remaining `<!--`, so nothing hidden (or marker-like) reaches GitHub."""
    while True:
        stripped = MARKER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.replace("<!--", "&lt;!--")


def redact(
    text: str,
    secrets: Sequence[str] = (),
) -> tuple[str, int]:
    """(text with secrets and well-known token formats replaced, number of replacements). `secrets` as secret_values
    gives them."""
    n = 0
    for v in sorted(secrets, key=len, reverse=True):
        if v in text:
            n += text.count(v)
            text = text.replace(v, REDACTED)
    for pattern in TOKEN_PATTERNS:
        text, k = pattern.subn(REDACTED, text)
        n += k
    text, k = AUTH_HEADER.subn(r"\1" + REDACTED, text)
    return text, n + k


def sanitize_report(
    text: str,
    secrets: Sequence[str] = (),
) -> tuple[str, int]:
    """(report safe to post, redactions): no comments (the agent can't forge or duplicate the marker), no secrets, within
    GitHub's size limit."""
    text, n = redact(neutralize_comments(text or ""), secrets)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n\n_[report truncated]_\n"
    return text.strip() + "\n", n


# The risk a change carries: each changed path takes the band of the module it belongs to, as rated at the change's base.


def unrated_module_name(
    path: str,
    names: Collection[str],
) -> str:
    """Name for a path outside every rated module: the first folder level that leaves the rated tree (or the file)."""
    parts = path.split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        if not any(n == prefix or n.startswith(prefix + "/") for n in names):
            return prefix
    return path


def change_risk(
    heatmap: Heatmap,
    paths: list[str],
    policy: Policy,
) -> ChangeRisk:
    """The ChangeRisk of changing `paths`, from the heat map of the change's base."""
    by_name = {m.name: m for m in heatmap.modules}
    files, touched, skipped = {}, {}, []
    for path in paths:
        if ignored(path, policy):
            files[path] = None
            skipped.append(path)
            continue
        name = module_of(path, by_name)
        files[path] = name
        if name:
            m = by_name[name]
            touched.setdefault(
                name,
                ModuleRisk(
                    module=name,
                    band=band(m.scores.overall),
                    score=m.scores.overall,
                    scores=m.scores.model_copy(),
                    evidence=m.evidence,
                    rated=True,
                    facts=m.facts,
                ),
            )
        else:
            key = unrated_module_name(path, by_name)
            touched.setdefault(key, ModuleRisk(module=key, band=policy.unrated_band, rated=False))
    risks = sorted(touched.values(), key=lambda risk: (-severity(risk.band), -(risk.score or 0), risk.module))
    scores = [r.score for r in risks if r.rated] + [band_floor(r.band) for r in risks if not r.rated]
    return ChangeRisk(
        files=files,
        modules=risks,
        ignored=skipped,
        max_band=risks[0].band if risks else "Low",
        max_score=max(scores, default=0),
    )


# The PR review CI trusts. The agent only proposes findings: the domain models check their shape, and the rules here
# hold them to the rules table (an unknown rule ID becomes NEW, a severity below its rule's is raised to it) and redact
# their text; the verdict, the blocker count and the band are derived, never taken from agent text.

RULE_ROW = re.compile(
    r"^\|\s*([A-Z]{1,4}-\d{2,3})\s*\|\s*(blocker|major|minor)\s*\|\s*(Low|Moderate|Medium|High|Critical)\s*\|", re.M
)
# The marker that ends every posted review; the eval reads it back to measure rule usefulness.
REVIEW_MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")


def rule_rows(
    text: str,
) -> list[ReviewRule]:
    return [
        ReviewRule.model_validate({"id": m.group(1), "severity": m.group(2), "min_tier": m.group(3)})
        for m in RULE_ROW.finditer(text)
    ]


def parse_rules(
    text: str,
) -> RulesTable:
    """The RulesTable of a rules file (rows of its Markdown tables)."""
    return RulesTable(rules=rule_rows(text))


def check_rules(
    text: str,
) -> CheckedRules:
    """The rules of a rules file an unattended eval may keep; a ValidationError when it has no rows or repeats an ID."""
    return CheckedRules(rules=rule_rows(text))


def rules_version(
    text: str,
) -> str:
    """Short id of a rules file: its git blob hash (as `git hash-object` gives it), 8 characters."""
    data = text.encode()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()[:8]


def finalize_review(
    agent: AgentReview,
    risk: RiskReport,
    rules: RulesTable,
    secrets: Sequence[str] = (),
) -> Review:
    """The Review CI trusts, from the agent's validated AgentReview, the RiskReport of the change and the base's rules;
    finding text is redacted of `secrets`."""
    known, findings, redactions = rules.by_id, [], 0
    for f in agent.findings:
        rule, severity, notes = f.rule, f.severity, []
        if rule != "NEW" and rule not in known:
            notes.append("cited unknown rule %s" % rule)
            rule = "NEW"
        if rule in known and SEVERITIES.index(severity) < SEVERITIES.index(known[rule].severity):
            notes.append("severity raised from %s to the rule's %s" % (severity, known[rule].severity))
            severity = known[rule].severity
        summary, n1 = redact(neutralize_comments(f.summary), secrets)
        fix, n2 = redact(neutralize_comments(f.fix), secrets)
        redactions += n1 + n2
        findings.append(
            Finding(rule=rule, severity=severity, file=f.file, line=f.line, summary=summary, fix=fix, notes=notes)
        )
    return Review(
        pr=risk.pr,
        base=risk.base,
        head=risk.head,
        max_tier=risk.max_tier,
        max_score=risk.max_score,
        rules_version=risk.rules_version,
        model_version=risk.model_version,
        findings=findings,
        redactions=redactions,
    )


def review_outputs(
    review: Review,
) -> StepOutputs:
    return StepOutputs(
        {
            "verdict": review.verdict,
            "max-tier": review.max_tier,
            "max-score": str(review.max_score),
            "blockers": str(review.blockers),
        }
    )


def marker(
    review: Review,
) -> str:
    rules = sorted({f.rule for f in review.findings} - {"NEW"})
    return "<!-- clean-eval:pr-review tier=%s score=%s rules=%s rules_version=%s -->" % (
        review.max_tier,
        review.max_score,
        ",".join(rules),
        review.rules_version or "",
    )


def compose_review(
    review: Review,
    report: str,
    on_diff: Optional[dict[str, set[int]]] = None,
) -> tuple[str, ReviewEvent, list[InlineComment]]:
    """(body, event, inline comments) of the review to post. Only findings on lines in the diff (`on_diff`: {path: lines},
    None when unknown) are attached inline, since GitHub rejects the whole review otherwise; the rest are listed in the
    body, which ends with the clean-eval marker."""
    comments, detached = [], []
    for f in review.findings:
        if not (f.file and f.line):
            continue
        if on_diff is None or f.line in on_diff.get(f.file, ()):
            comments.append(
                InlineComment(
                    path=f.file,
                    line=f.line,
                    side="RIGHT",
                    body="[%s][%s] %s" % (f.rule, f.severity, f.summary) + ("\n\n**Fix:** %s" % f.fix if f.fix else ""),
                )
            )
        else:
            detached.append("- [%s][%s] `%s:%s`: %s" % (f.rule, f.severity, f.file, f.line, f.summary))
    extra = ("\n\n**Not attached inline** (line is outside the diff):\n" + "\n".join(detached)) if detached else ""
    body = "%s%s\n\n%s" % (report.strip(), extra, marker(review))
    event = "REQUEST_CHANGES" if review.verdict == "request_changes" else "COMMENT"
    return body, event, comments


def post_attempts(
    event: ReviewEvent,
    comments: list[InlineComment],
) -> list[tuple[ReviewEvent, list[InlineComment]]]:
    """(event, comments) to try in turn: GitHub rejects inline comments on lines outside the diff (422), and a reviewer
    can't request changes on its own PR."""
    attempts = []
    for attempt in ((event, comments), (event, []), ("COMMENT", comments), ("COMMENT", [])):
        if attempt not in attempts:
            attempts.append(attempt)
    return attempts


def stale_reviews(
    reviews: list[HostReview],
    me: Optional[str],
    my_id: Optional[int],
) -> list[int]:
    """IDs of this reviewer's earlier "changes requested" clean-eval reviews (HostReviews), which `my_id` supersedes."""
    if not me:
        return []
    return [
        r.id
        for r in reviews
        if r.id is not None
        and r.id != my_id
        and r.state == "CHANGES_REQUESTED"
        and r.user == me
        and MARKER_RE.search(r.body)
    ]
