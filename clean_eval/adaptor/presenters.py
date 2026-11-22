"""The presenters: the README's SVG treemap and the block around it (a DiagramPresenter), and the Markdown summary and
review comment (a ReportPresenter). Rendering only, never I/O."""

from collections.abc import Sequence
from typing import Optional
from xml.sax.saxutils import escape

from clean_eval.domain.policy import (
    BANDS,
    Aspect,
    Band,
)
from clean_eval.domain.rating import (
    Heatmap,
    ModuleRisk,
)
from clean_eval.domain.review import (
    Finding,
    ReviewComment,
    RuleViolation,
    Verdict,
)
from clean_eval.use_cases.interfaces import (
    DiagramPresenter,
    ReportPresenter,
)
from clean_eval.use_cases.rules.heatmap import (
    MARKERS,
    band,
)

# Presenters: adaptors that turn a heatmap into the SVG diagram, the README block and a Markdown summary.


# How bands look: the status palette, emoji and glyphs shared by the SVG and the Markdown summary.

# band -> (colour, emoji, glyph). Tiles are drawn translucent: a tint of the colour behind theme-coloured text, with a
# solid border.
BAND_STYLE = frozendict(
    {
        Band.LOW: ("#0ca30c", "🟢", "✓"),
        Band.MODERATE: ("#fab219", "🟡", "!"),
        Band.HIGH: ("#ec835a", "🟠", "▲"),
        Band.CRITICAL: ("#d03b3b", "🔴", "✕"),
        "N/A": ("#898781", "⚪", "–"),
    }
)
TILE_OPACITY = 0.35
DIAGRAM_BACKING, DIAGRAM_TEXT = (
    "#0d1117",
    "#e6edf3",
)  # the SVG brings its own dark backing: GitHub's page theme can differ from the OS theme
MIN_TILE_SHARE = 0.015  # tiles under this share of the code are drawn larger so they stay readable


# Squarified treemap layout.


def worst_aspect_ratio[T](
    row: list[tuple[T, float]],
    side: float,
) -> float:
    """The worst (largest) aspect ratio of `row`'s tiles, [(payload, area)], laid along a side of length `side`:
    squarify adds a tile to a row only while this doesn't get worse."""
    s, mx, mn = sum(a for _, a in row), max(a for _, a in row), min(a for _, a in row)
    return max(side * side * mx / (s * s), s * s / (side * side * mn))


def squarify[T](
    items: Sequence[tuple[T, float]],
    left: float,
    top: float,
    width: float,
    height: float,
) -> list[tuple[T, float, float, float, float]]:
    """`items` is [(payload, area)] sorted by area descending; returns [(payload, x, y, w, h)] filling the rectangle."""
    out, rest = [], list(items)
    while rest:
        side, row, i = min(width, height), [rest[0]], 1
        while i < len(rest) and worst_aspect_ratio(row + [rest[i]], side) <= worst_aspect_ratio(row, side):
            row.append(rest[i])
            i += 1
        s = sum(a for _, a in row)
        if width >= height:
            cw, cy = s / height, top
            for p, a in row:
                out.append((p, left, cy, cw, a / cw))
                cy += a / cw
            left, width = left + cw, width - cw
        else:
            rh, cx = s / width, left
            for p, a in row:
                out.append((p, cx, top, a / rh, rh))
                cx += a / rh
            top, height = top + rh, height - rh
        rest = rest[len(row) :]
    return out


# DiagramPresenter: a static SVG treemap, and the README block that shows it.

WIDTH, HEIGHT = 960, 540
TEXT_MIN_W, TEXT_MIN_H = 96, 44
COLUMNS = tuple((aspect.score_key, aspect.label) for aspect in Aspect)


def band_label(
    score: Optional[int],
) -> str:
    return "N/A" if score is None else band(score)


def format_loc(
    lines: int,
) -> str:
    return "%dk" % round(lines / 1000) if lines >= 10000 else "%.1fk" % (lines / 1000) if lines >= 1000 else str(lines)


def legend() -> str:
    return " · ".join(
        "%s %s %d-%d" % (BAND_STYLE[n][1], n, (BANDS[i - 1][0] + 1) if i else 0, hi) for i, (hi, n) in enumerate(BANDS)
    )


class SvgDiagramPresenter(DiagramPresenter):
    def svg(
        self,
        heatmap: Heatmap,
    ) -> str:
        mods = sorted(heatmap.modules, key=lambda module: -module.loc)
        total = sum(m.loc for m in mods) or 1
        weights = [max(m.loc, total * MIN_TILE_SHARE) for m in mods]
        scale = WIDTH * HEIGHT / (sum(weights) or 1)
        rects = squarify([(m, w * scale) for m, w in zip(mods, weights)], 0, 0, WIDTH, HEIGHT)
        out = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="t d" '
            'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' % (WIDTH, HEIGHT, WIDTH, HEIGHT),
            '<title id="t">%s risk heat map</title><desc id="d">Tile area is lines of code; colour is the overall risk band at %s.</desc>'
            % (escape(heatmap.repo), escape(heatmap.sha)),
            "<style>.t{fill:%s}</style>" % DIAGRAM_TEXT,
            '<rect width="%d" height="%d" rx="6" fill="%s"/>' % (WIDTH, HEIGHT, DIAGRAM_BACKING),
        ]
        for m, x, y, w, h in rects:
            name = band_label(m.scores.overall)
            colour, _, glyph = BAND_STYLE[name]
            detail = " · ".join(
                "%s %s" % (label, "N/A" if getattr(m.scores, k) is None else getattr(m.scores, k))
                for k, label in COLUMNS
            )
            out.append(
                "<g><title>%s: overall %s (%s), %s lines. %s</title>"
                % (escape(m.name), m.scores.overall, name, m.loc, escape(detail))
            )
            out.append(
                '<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" fill="%s" fill-opacity="%s" stroke="%s" stroke-width="2"/>'
                % (x + 2, y + 2, max(w - 4, 1), max(h - 4, 1), colour, TILE_OPACITY, colour)
            )
            if w >= TEXT_MIN_W and h >= TEXT_MIN_H:
                short = "/".join(m.name.split("/")[-2:])
                room = max(int((w - 16) / 7), 4)
                short = short if len(short) <= room else short[: room - 1] + "…"
                out.append(
                    '<text class="t" x="%.1f" y="%.1f" font-size="13" font-weight="600">%s</text>'
                    % (x + 10, y + 22, escape(short))
                )
                out.append(
                    '<text class="t" x="%.1f" y="%.1f" font-size="12">%s %s · %s</text>'
                    % (x + 10, y + 39, glyph, m.scores.overall, format_loc(m.loc))
                )
            out.append("</g>")
        out.append("</svg>")
        return "\n".join(out) + "\n"

    def readme_block(
        self,
        heatmap: Heatmap,
        image_link: str,
        fingerprint: str,
    ) -> str:
        start, end = MARKERS
        return "\n".join(
            [
                start,
                "<!-- risk-heat-map:fingerprint=%s -->" % fingerprint,
                "",
                "![Risk heat map of %s at %s](%s)" % (heatmap.repo, heatmap.sha, image_link),
                "",
                "Tile area is module size (lines of code, tests excluded); colour is the overall risk of changing it. Heuristics from "
                "static analysis of `%s` at `%s` (%s), not a verdict. Regenerate with the `risk-heat-map` skill."
                % (heatmap.ref, heatmap.sha, heatmap.generated_at),
                "",
                legend(),
                end,
                "",
            ]
        )


# ReportPresenter: a short Markdown summary of a heatmap, for job summaries and chat.

TOP = 10


def score_cell(
    score: Optional[int],
) -> str:
    if score is None:
        return "%s N/A" % BAND_STYLE["N/A"][1]
    return "%s %d" % (BAND_STYLE[band(score)][1], score)


def markdown_name(
    name: str,
) -> str:
    return "`%s`" % name.replace("`", "'").replace("|", "\\|")


# A review's comment: the one layout every review comment has (the pr-review skill's references/comment.md shows it).

VERDICT_LABELS = frozendict(
    {
        Verdict.APPROVE: "Approve",
        Verdict.COMMENT: "Approve with comments",
        Verdict.REQUEST_CHANGES: "Request changes",
    }
)
MODULES_SHOWN = 5  # the riskiest modules a comment names; it counts the rest
SUMMARY_COLUMNS = ("Risk", "Modules", "Rules checked", "Findings")
VIOLATION_COLUMNS = ("Rule", "Severity", "Where", "Finding", "Fix")
UNCOVERED_COLUMNS = ("Severity", "Where", "Finding", "Fix")


def table_cell(
    text: str,
) -> str:
    """Text as a Markdown table's cell holds it: on one line, with its pipes escaped."""
    return " ".join(text.split()).replace("|", "\\|")


def table(
    columns: Sequence[str],
    rows: list[list[str]],
) -> list[str]:
    """A Markdown table's lines: its header, then a line per row."""
    return [
        "| %s |" % " | ".join(columns),
        "|%s" % ("---|" * len(columns)),
        *("| %s |" % " | ".join(row) for row in rows),
    ]


def finding_cells(
    finding: Finding,
) -> list[str]:
    """A finding's cells in a comment's table: its severity, where it is, what is wrong (and what the checks changed),
    and the fix."""
    where = markdown_name(finding.file + (":%d" % finding.line if finding.line else "")) if finding.file else "—"
    notes = " _(%s)_" % table_cell("; ".join(finding.notes)) if finding.notes else ""
    return [str(finding.severity), where, table_cell(finding.summary) + notes, table_cell(finding.fix) or "—"]


def violation_rows(
    violation: RuleViolation,
) -> list[list[str]]:
    """A violated rule's rows in a comment's table: one per finding that cites it, each naming the rule."""
    rule = "**%s** %s" % (violation.rule, table_cell(violation.title)) if violation.title else "**%s**" % violation.rule
    return [[rule, *finding_cells(f)] for f in violation.findings]


def modules_cell(
    modules: list[ModuleRisk],
) -> str:
    """The modules a change touches, the riskiest first, each with its band and score (or as unrated)."""
    named = [
        "%s %s %s"
        % (markdown_name(m.module), BAND_STYLE[m.band][1], m.score if m.rated and m.score is not None else "unrated")
        for m in modules[:MODULES_SHOWN]
    ]
    more = [" and %d more" % (len(modules) - MODULES_SHOWN)] if len(modules) > MODULES_SHOWN else []
    return " · ".join(named) + "".join(more) if named else "none"


class MarkdownReportPresenter(ReportPresenter):
    def report(
        self,
        heatmap: Heatmap,
    ) -> str:
        mods = sorted(heatmap.modules, key=lambda module: (-module.scores.overall, module.name))
        head = [
            "**%s** risk heat map · `%s` @ `%s` · %s · %d modules · %d lines%s"
            % (
                heatmap.repo,
                heatmap.ref,
                heatmap.sha,
                heatmap.generated_at,
                len(mods),
                heatmap.loc_total,
                " · %d levelled by a read-through" % heatmap.judged_modules if heatmap.judged_modules else "",
            ),
            "",
        ]
        if not mods:
            return "\n".join(head + ["_No Python source modules to rate._", ""])
        cols = ["Module", "Overall"] + [aspect.label for aspect in Aspect] + ["Lines"]
        rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for m in mods[:TOP]:
            rows.append(
                "| %s | %s | %s | %d |"
                % (
                    markdown_name(m.name),
                    score_cell(m.scores.overall),
                    " | ".join(score_cell(getattr(m.scores, aspect.score_key)) for aspect in Aspect),
                    m.loc,
                )
            )
        tail = ["", "_%d more modules in the full heat map._" % (len(mods) - TOP)] if len(mods) > TOP else []
        tail += ["", legend()]
        notes = ["", "Notes: " + "; ".join(heatmap.notes) + "."] if heatmap.notes else []
        return "\n".join(head + rows + tail + notes) + "\n"

    def comment(
        self,
        comment: ReviewComment,
    ) -> str:
        summary = [
            "%s %s, score %d" % (BAND_STYLE[comment.max_tier][1], comment.max_tier, comment.max_score),
            modules_cell(comment.modules),
            "%d of %d (min tier up to %s)" % (comment.rules_checked, comment.rules_total, comment.max_tier),
            " · ".join("%d %s" % (count, severity) for severity, count in comment.severity_counts.items()),
        ]
        violated = [row for violation in comment.violations for row in violation_rows(violation)]
        uncovered = [finding_cells(f) for f in comment.uncovered]
        lines = [
            "## clean-eval review: %s" % VERDICT_LABELS[comment.verdict],
            "",
            *table(SUMMARY_COLUMNS, [summary]),
            "",
            "### Rules violated",
            "",
            *(table(VIOLATION_COLUMNS, violated) if violated else ["None."]),
            "",
            "### Not covered by a rule",
            "",
            *(table(UNCOVERED_COLUMNS, uncovered) if uncovered else ["None."]),
            "",
            "### Notes",
            "",
            comment.notes.strip() or "_No notes._",
        ]
        return "\n".join(lines) + "\n"
