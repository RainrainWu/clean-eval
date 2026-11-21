"""DiagramPresenter: a static SVG treemap, and the README block that shows it."""
from xml.sax.saxutils import escape

from ...domain.policy import ASPECTS, BANDS
from ...use_cases.rules.readme import MARKERS
from ...use_cases.rules.scoring import band
from . import style
from .treemap import squarify

WIDTH, HEIGHT = 960, 540
TEXT_MIN_W, TEXT_MIN_H = 96, 44
COLUMNS = tuple((key, label) for _, key, label, _, _ in ASPECTS)


def _band(score):
    return "N/A" if score is None else band(score)


def _fmt(n):
    return "%dk" % round(n / 1000) if n >= 10000 else "%.1fk" % (n / 1000) if n >= 1000 else str(n)


def legend():
    return " · ".join("%s %s %d-%d" % (style.BAND_STYLE[n][1], n, (BANDS[i - 1][0] + 1) if i else 0, hi)
                      for i, (hi, n) in enumerate(BANDS))


class SvgDiagramPresenter:
    def svg(self, heatmap):
        mods = sorted(heatmap.modules, key=lambda m: -m.loc)
        total = sum(m.loc for m in mods) or 1
        weights = [max(m.loc, total * style.MIN_TILE_SHARE) for m in mods]
        scale = WIDTH * HEIGHT / (sum(weights) or 1)
        rects = squarify([(m, w * scale) for m, w in zip(mods, weights)], 0, 0, WIDTH, HEIGHT)
        out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="t d" '
               'font-family="system-ui, -apple-system, Segoe UI, sans-serif">' % (WIDTH, HEIGHT, WIDTH, HEIGHT),
               '<title id="t">%s risk heat map</title><desc id="d">Tile area is lines of code; colour is the overall risk band at %s.</desc>'
               % (escape(heatmap.repo), escape(heatmap.sha)),
               "<style>.t{fill:%s}</style>" % style.DIAGRAM_TEXT,
               '<rect width="%d" height="%d" rx="6" fill="%s"/>' % (WIDTH, HEIGHT, style.DIAGRAM_BACKING)]
        for m, x, y, w, h in rects:
            name = _band(m.scores.overall)
            colour, _, glyph = style.BAND_STYLE[name]
            detail = " · ".join("%s %s" % (label, "N/A" if getattr(m.scores, k) is None else getattr(m.scores, k))
                                for k, label in COLUMNS)
            out.append("<g><title>%s: overall %s (%s), %s lines. %s</title>" % (escape(m.name), m.scores.overall, name, m.loc, escape(detail)))
            out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="4" fill="%s" fill-opacity="%s" stroke="%s" stroke-width="2"/>'
                       % (x + 2, y + 2, max(w - 4, 1), max(h - 4, 1), colour, style.TILE_OPACITY, colour))
            if w >= TEXT_MIN_W and h >= TEXT_MIN_H:
                short = "/".join(m.name.split("/")[-2:])
                room = max(int((w - 16) / 7), 4)
                short = short if len(short) <= room else short[: room - 1] + "…"
                out.append('<text class="t" x="%.1f" y="%.1f" font-size="13" font-weight="600">%s</text>' % (x + 10, y + 22, escape(short)))
                out.append('<text class="t" x="%.1f" y="%.1f" font-size="12">%s %s · %s</text>' % (x + 10, y + 39, glyph, m.scores.overall, _fmt(m.loc)))
            out.append("</g>")
        out.append("</svg>")
        return "\n".join(out) + "\n"

    def readme_block(self, heatmap, image_link, fingerprint):
        start, end = MARKERS
        return "\n".join([
            start, "<!-- risk-heat-map:fingerprint=%s -->" % fingerprint, "",
            "![Risk heat map of %s at %s](%s)" % (heatmap.repo, heatmap.sha, image_link), "",
            "Tile area is module size (lines of code, tests excluded); colour is the overall risk of changing it. Heuristics from "
            "static analysis of `%s` at `%s` (%s), not a verdict. Regenerate with the `risk-heat-map` skill." % (heatmap.ref, heatmap.sha, heatmap.generated_at), "",
            legend(), end, ""])
