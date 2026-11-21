"""PagePresenter: the interactive heat-map page, built by filling a template with the heatmap and the shared settings."""
import dataclasses
import json

from ...domain.policy import ASPECTS, BANDS
from . import style


class HtmlPagePresenter:
    def __init__(self, template):
        self.template = template

    def present(self, heatmap, suffix=""):
        data = dataclasses.asdict(heatmap)
        for m in data["modules"]:
            m.pop("facts", None)  # the page shows scores and evidence; the raw facts are for calibration only
        data["aspects"] = [{"key": key, "label": label, "id": tab, "levels": levels} for _, key, label, tab, levels in ASPECTS]
        data["bands"] = [{"max": hi, "label": name, "glyph": style.BAND_STYLE[name][2]} for hi, name in BANDS]
        payload = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
        title = "%s Risk Heat Map%s" % (heatmap.repo, suffix)
        return self.template.replace("__TITLE__", title).replace("/*__BAND_VARS__*/", self._band_css()).replace("__DATA__", payload)

    @staticmethod
    def _band_css():
        out = []
        for band, css in style.CSS_NAMES:
            colour = style.BAND_STYLE[band][0]
            r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
            out.append("--%s: %s; --%s-rgb: %d,%d,%d;" % (css, colour, css, r, g, b))
        return " ".join(out)
