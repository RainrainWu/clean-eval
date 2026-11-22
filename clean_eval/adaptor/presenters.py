"""The presenters: the README's SVG treemap and the block around it (a DiagramPresenter), and the Markdown summary
(a ReportPresenter). Rendering only, never I/O."""

from collections.abc import Sequence
from typing import Optional
from xml.sax.saxutils import escape

from clean_eval.domain.policy import ASPECTS, BANDS
from clean_eval.domain.rating import Heatmap
from clean_eval.use_cases.interfaces import DiagramPresenter, ReportPresenter
from clean_eval.use_cases.rules.heatmap import MARKERS, band

# Presenters: adaptors that turn a heatmap into the SVG diagram, the README block and a Markdown summary.


# How bands look: the status palette, emoji and glyphs shared by the SVG and the Markdown summary.

# band -> (colour, emoji, glyph). Tiles are drawn translucent: a tint of the colour behind theme-coloured text, with a solid border.
BAND_STYLE = {
    "Low": ("#0ca30c", "🟢", "✓"),
    "Moderate": ("#fab219", "🟡", "!"),
    "High": ("#ec835a", "🟠", "▲"),
    "Critical": ("#d03b3b", "🔴", "✕"),
    "N/A": ("#898781", "⚪", "–"),
}
TILE_OPACITY = 0.35
DIAGRAM_BACKING, DIAGRAM_TEXT = (
    "#0d1117",
    "#e6edf3",
)  # the SVG brings its own dark backing: GitHub's page theme can differ from the OS theme
MIN_TILE_SHARE = 0.015  # tiles under this share of the code are drawn larger so they stay readable
DIAGRAM_PATH = "docs/risk-heatmap.svg"  # default location of the README diagram, relative to the repository root


# Squarified treemap layout.


def squarify[T](
    items: Sequence[tuple[T, float]],
    left: float,
    top: float,
    width: float,
    height: float,
) -> list[tuple[T, float, float, float, float]]:
    """`items` is [(payload, area)] sorted by area descending; returns [(payload, x, y, w, h)] filling the rectangle."""
    out, rest = [], list(items)

    def worst(
        row: list[tuple[T, float]],
        side: float,
    ) -> float:
        s, mx, mn = sum(a for _, a in row), max(a for _, a in row), min(a for _, a in row)
        return max(side * side * mx / (s * s), s * s / (side * side * mn))

    while rest:
        side, row, i = min(width, height), [rest[0]], 1
        while i < len(rest) and worst(row + [rest[i]], side) <= worst(row, side):
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
COLUMNS = tuple((key, label) for _, key, label, _, _ in ASPECTS)


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
        cols = ["Module", "Overall"] + [label for _, _, label, _, _ in ASPECTS] + ["Lines"]
        rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for m in mods[:TOP]:
            rows.append(
                "| %s | %s | %s | %d |"
                % (
                    markdown_name(m.name),
                    score_cell(m.scores.overall),
                    " | ".join(score_cell(getattr(m.scores, k)) for _, k, _, _, _ in ASPECTS),
                    m.loc,
                )
            )
        tail = ["", "_%d more modules in the full heat map._" % (len(mods) - TOP)] if len(mods) > TOP else []
        tail += ["", legend()]
        notes = ["", "Notes: " + "; ".join(heatmap.notes) + "."] if heatmap.notes else []
        return "\n".join(head + rows + tail + notes) + "\n"
