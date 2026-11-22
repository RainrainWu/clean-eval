"""The PR review's rules: text cut to size, redaction of what leaves the runner, a change's risk from the heat map of
its base, the rules a review applies, and the review CI derives from what the agent proposed."""

import fnmatch
import re
from collections.abc import (
    Collection,
    Mapping,
)
from typing import Optional

from clean_eval.domain.policy import (
    Band,
    Policy,
)
from clean_eval.domain.rating import (
    ChangeRisk,
    Heatmap,
    ModuleRisk,
)
from clean_eval.domain.review import (
    AgentReview,
    CheckedRules,
    Finding,
    HostReview,
    InlineComment,
    Review,
    ReviewComment,
    ReviewRule,
    RiskReport,
    RuleSelection,
    RulesTable,
    RuleViolation,
    Secrets,
    StepOutputs,
    Verdict,
)
from clean_eval.use_cases.rules.analysis import (
    ignored,
    module_of,
)
from clean_eval.use_cases.rules.heatmap import (
    band,
    band_floor,
    severity,
)

# Text cut to size.


def truncate(
    text: str,
    limit: int,
) -> tuple[str, bool]:
    """(text cut to `limit` characters with a note saying how much is missing, whether it was cut)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n\n[... truncated: %d more characters ...]\n" % (len(text) - limit), True


# What may leave the CI runner. Agent text and tool output can carry secrets (read from the environment or the checkout)
# and hidden instructions, so everything posted or written out is cleaned first: the secrets the environment names
# (values, and the patterns of well-known credentials) become [REDACTED], and HTML comments are defanged, so nothing
# hidden or marker-like reaches the code host.

REDACTED = "[REDACTED]"
MARKER_RE = re.compile(r"<!--\s*clean-eval:[^>]*-->")
MAX_REPORT_CHARS = 60_000  # a longer report is cut, so it fits in a review: code hosts cap a review's size


def neutralize_comments(
    text: str,
) -> str:
    """Agent text may not carry HTML comments: strip clean-eval markers until none reassemble from nested input, then
    defang every remaining `<!--`, so nothing hidden (or marker-like) reaches the code host."""
    while True:
        stripped = MARKER_RE.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text.replace("<!--", "&lt;!--")


def masked(
    found: re.Match[str],
) -> str:
    """What a secret pattern's match becomes: REDACTED in place of the pattern's group when it has one (the secret the
    rest frames), else in place of all of it."""
    if not found.re.groups or found.start(1) < 0:
        return REDACTED
    whole, offset = found.group(0), found.start()
    return whole[: found.start(1) - offset] + REDACTED + whole[found.end(1) - offset :]


def redact(
    text: str,
    secrets: Secrets,
) -> tuple[str, int]:
    """(text with the `secrets` replaced, number of replacements): their values, longest first so a secret that holds
    another is replaced whole, then the matches of their patterns."""
    n = 0
    for v in sorted(secrets.values, key=len, reverse=True):
        if v in text:
            n += text.count(v)
            text = text.replace(v, REDACTED)
    for pattern in secrets.patterns:
        text, k = pattern.subn(masked, text)
        n += k
    return text, n


def sanitize_report(
    text: str,
    secrets: Secrets,
) -> tuple[str, int]:
    """(report safe to post, redactions): no comments (the agent can't forge or duplicate the marker), no secrets,
    within MAX_REPORT_CHARS."""
    text, n = redact(neutralize_comments(text or ""), secrets)
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n\n_[report truncated]_\n"
    return text.strip() + "\n", n


# The risk a change carries: each changed path takes the band of its module, as rated at the change's base.


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
            touched.setdefault(
                key,
                ModuleRisk(
                    module=key,
                    band=policy.unrated_band,
                    rated=False,
                ),
            )
    risks = sorted(touched.values(), key=lambda risk: (-severity(risk.band), -(risk.score or 0), risk.module))
    scores = [r.score for r in risks if r.rated] + [band_floor(r.band) for r in risks if not r.rated]
    return ChangeRisk(
        files=files,
        modules=risks,
        ignored=skipped,
        max_band=risks[0].band if risks else Band.LOW,
        max_score=max(scores, default=0),
    )


# The rules a review applies. They come in packs, one Markdown file each: clean-eval's, and the repository's own over
# them (a pack replaces clean-eval's pack of its name, or adds one), combined into one text with the policy pack first.
# A rule is a row of a pack's tables, `| ID | severity | min tier | rule |`; a selection keeps only the rows it applies.

RULE_ROW = re.compile(
    r"^\|\s*([A-Z]{1,4}-\d{2,3})\s*\|\s*(blocker|major|minor)\s*\|\s*(Low|Moderate|Medium|High|Critical)\s*\|(.*)$",
    re.M,
)
RULE_TITLE = re.compile(r"\s*\*\*(.+?)\*\*")  # a rule's title: the bold lead of its text, `**Dependency rule.**`
POLICY_PACK = "policy.md"  # the pack read first: the rule fields and the tier policy
RULE_PACK = re.compile(r"[A-Za-z0-9_-]+\.md")  # a pack's file name


def rule_title(
    text: str,
) -> str:
    """A rule's title: the bold lead of its text, without its full stop; empty when it has none."""
    lead = RULE_TITLE.match(text)
    return lead.group(1).strip().rstrip(".") if lead else ""


def rule_rows(
    text: str,
) -> list[ReviewRule]:
    return [
        ReviewRule.model_validate(
            {"id": m.group(1), "severity": m.group(2), "min_tier": m.group(3), "title": rule_title(m.group(4))}
        )
        for m in RULE_ROW.finditer(text)
    ]


def parse_rules(
    text: str,
) -> RulesTable:
    """The RulesTable of a rules text (rows of its Markdown tables)."""
    return RulesTable(
        rules=rule_rows(text),
    )


def check_rules(
    text: str,
) -> CheckedRules:
    """The rules of a rules text an unattended retro may keep; a ValidationError when it has no rows or repeats an ID."""
    return CheckedRules(
        rules=rule_rows(text),
    )


def is_rule_pack(
    name: str,
) -> bool:
    """Whether a file named `name` is a rule pack: <letters, digits, - or _>.md."""
    return RULE_PACK.fullmatch(name) is not None


def combine_rule_packs(
    default_packs: Mapping[str, str],
    repo_packs: Mapping[str, str],
) -> str:
    """The packs' rules as one text ({file name: text} each): the repository's packs replace clean-eval's of the same
    name, and the policy pack comes first, the rest by name."""
    packs = {**default_packs, **repo_packs}
    names = sorted(packs, key=lambda name: (name != POLICY_PACK, name))
    return "\n".join(packs[name].rstrip("\n") + "\n" for name in names)


def duplicate_rule_ids(
    text: str,
) -> list[str]:
    """The rule IDs a rules text defines more than once, sorted."""
    ids = [row.group(1) for row in RULE_ROW.finditer(text)]
    return sorted({rule_id for rule_id in ids if ids.count(rule_id) > 1})


def applies(
    rule_id: str,
    selection: RuleSelection,
) -> bool:
    """Whether a review applies the rule `rule_id`: when the selection includes any rule, only if it includes this one
    (include supersedes exclude); else unless it excludes it."""
    if selection.include:
        return any(fnmatch.fnmatchcase(rule_id, name) for name in selection.include)
    return not any(fnmatch.fnmatchcase(rule_id, name) for name in selection.exclude)


def select_rules(
    text: str,
    selection: RuleSelection,
) -> tuple[str, list[str]]:
    """The rules text with only the rule rows the selection applies (the rest of the text as it is), and notes on the
    selection: each name that matches no rule, and an exclude list that an include list supersedes."""
    ids = [row.group(1) for row in RULE_ROW.finditer(text)]
    kept = []
    for line in text.splitlines(keepends=True):
        row = RULE_ROW.match(line)
        if row is None or applies(row.group(1), selection):
            kept.append(line)
    names = [*selection.include, *selection.exclude]
    notes = ["no rule matches %s" % name for name in names if not any(fnmatch.fnmatchcase(i, name) for i in ids)]
    if selection.include and selection.exclude:
        notes.append(
            "the excluded rules (%s) are ignored: an include list supersedes them" % ", ".join(selection.exclude)
        )
    return "".join(kept), notes


# The PR review CI trusts. The agent only proposes findings: the domain models check their shape, and the rules here
# hold them to the rules table (an unknown rule ID becomes NEW, a severity below its rule's is raised to it) and redact
# their text; the verdict, the blocker count and the band are derived, never taken from agent text.

# The marker that ends every posted review; the retro reads it back to measure rule usefulness.
REVIEW_MARKER = re.compile(r"<!--\s*clean-eval:pr-review\s+([^>]*)-->")


def finalize_review(
    agent: AgentReview,
    risk: RiskReport,
    rules: RulesTable,
    secrets: Secrets,
) -> Review:
    """The Review CI trusts, from the agent's validated AgentReview, the RiskReport of the change and the base's rules;
    finding text is redacted of `secrets`."""
    known, findings, redactions = rules.by_id, [], 0
    for f in agent.findings:
        rule, severity, notes = f.rule, f.severity, []
        if rule != "NEW" and rule not in known:
            notes.append("cited unknown rule %s" % rule)
            rule = "NEW"
        if rule in known and severity.rank < known[rule].severity.rank:
            notes.append("severity raised from %s to the rule's %s" % (severity, known[rule].severity))
            severity = known[rule].severity
        summary, n1 = redact(neutralize_comments(f.summary), secrets)
        fix, n2 = redact(neutralize_comments(f.fix), secrets)
        redactions += n1 + n2
        findings.append(
            Finding(
                rule=rule,
                severity=severity,
                file=f.file,
                line=f.line,
                summary=summary,
                fix=fix,
                notes=notes,
            )
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


def review_comment(
    review: Review,
    risk: RiskReport,
    rules: RulesTable,
    notes: str,
) -> ReviewComment:
    """What the comment on the change says: the verdict and risk of the `review`, how many `rules` its band reaches (min
    tier up to it), the rules its findings violate (each with its findings, the most severe first, then by ID), the
    findings no rule covers, and the reviewer's `notes`."""
    known, cited = rules.by_id, {}
    for finding in review.findings:
        if finding.rule != "NEW":
            cited.setdefault(finding.rule, []).append(finding)
    violations = [
        RuleViolation(
            rule=rule,
            title=known[rule].title if rule in known else "",
            severity=max((f.severity for f in findings), key=lambda severity: severity.rank),
            findings=findings,
        )
        for rule, findings in cited.items()
    ]
    return ReviewComment(
        verdict=review.verdict,
        max_tier=review.max_tier,
        max_score=review.max_score,
        modules=risk.modules,
        rules_checked=sum(rule.min_tier.rank <= review.max_tier.rank for rule in known.values()),
        rules_total=len(known),
        violations=sorted(violations, key=lambda violation: (-violation.severity.rank, violation.rule)),
        uncovered=[f for f in review.findings if f.rule == "NEW"],
        notes=notes,
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
    comment: str,
    on_diff: Optional[dict[str, set[int]]] = None,
) -> tuple[str, bool, list[InlineComment]]:
    """(body, whether it requests changes, inline comments) of the review to post: the body is the review's `comment`
    (which lists every finding) and ends with the clean-eval marker. Only findings on lines in the diff (`on_diff`:
    {path: lines}, None when unknown) are attached inline too, since a code host takes inline comments only there."""
    comments = [
        InlineComment(
            path=f.file,
            line=f.line,
            body="[%s][%s] %s" % (f.rule, f.severity, f.summary) + ("\n\n**Fix:** %s" % f.fix if f.fix else ""),
        )
        for f in review.findings
        if f.file and f.line and (on_diff is None or f.line in on_diff.get(f.file, ()))
    ]
    return "%s\n\n%s" % (comment.strip(), marker(review)), review.verdict == Verdict.REQUEST_CHANGES, comments


def stale_reviews(
    reviews: list[HostReview],
    me: Optional[str],
    my_id: Optional[int],
) -> list[int]:
    """IDs of this reviewer's earlier clean-eval reviews requesting changes (HostReviews), which `my_id` supersedes."""
    if not me:
        return []
    return [
        r.id
        for r in reviews
        if r.id is not None and r.id != my_id and r.requests_changes and r.author == me and MARKER_RE.search(r.body)
    ]
