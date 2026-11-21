"""ReportPresenter: a short Markdown summary of a heatmap, for job summaries and chat."""
from ...domain.policy import ASPECTS
from ...domain.scoring import band
from . import style
from .diagram import legend

TOP = 10


def _cell(score):
    if score is None:
        return "%s N/A" % style.BAND_STYLE["N/A"][1]
    return "%s %d" % (style.BAND_STYLE[band(score)][1], score)


def _name(name):
    return "`%s`" % name.replace("`", "'").replace("|", "\\|")


class MarkdownReportPresenter:
    def report(self, heatmap):
        mods = sorted(heatmap.modules, key=lambda m: (-m.scores["overall"], m.name))
        head = ["**%s** risk heat map · `%s` @ `%s` · %s · %d modules · %d lines%s"
                % (heatmap.repo, heatmap.ref, heatmap.sha, heatmap.generated_at, len(mods), heatmap.loc_total,
                   " · %d levelled by a read-through" % heatmap.judged_modules if heatmap.judged_modules else ""), ""]
        if not mods:
            return "\n".join(head + ["_No Python source modules to rate._", ""])
        cols = ["Module", "Overall"] + [label for _, _, label, _, _ in ASPECTS] + ["Lines"]
        rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for m in mods[:TOP]:
            rows.append("| %s | %s | %s | %d |" % (_name(m.name), _cell(m.scores["overall"]),
                                                   " | ".join(_cell(m.scores[k]) for _, k, _, _, _ in ASPECTS), m.loc))
        tail = ["", "_%d more modules in the full heat map._" % (len(mods) - TOP)] if len(mods) > TOP else []
        tail += ["", legend()]
        notes = ["", "Notes: " + "; ".join(heatmap.notes) + "."] if heatmap.notes else []
        return "\n".join(head + rows + tail + notes) + "\n"
