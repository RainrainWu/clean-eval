"""How well the risk scores predicted escaped defects (AUC, escape rate per band, per aspect), and replays of a candidate
policy on an already-collected history: every rated module is re-scored from its stored facts, so a change of weights,
mixes or thresholds is evaluated in milliseconds without touching git. Module shape (depth, paths) needs a re-collect."""
import dataclasses
from collections import defaultdict

from .policy import BAND_NAMES, SCORE_KEYS
from .rating import rescore as rescore_module
from .scoring import band, band_floor, severity

SENSITIVITY_GROUPS = ("business_weights", "effects_mix", "entropy_weights", "unverified_mix")


def auc(pos, neg):
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def _aspect(u, key):
    """Highest score of one aspect among the rated modules a PR touched (0 when none)."""
    return max([m["scores"].get(key) or 0 for m in u["modules"] if m.get("rated")] or [0])


def calibration(prs):
    by_band = defaultdict(lambda: {"prs": 0, "defective_prs": 0})
    for u in prs:
        by_band[u["max_tier"]]["prs"] += 1
        by_band[u["max_tier"]]["defective_prs"] += bool(u["defects"])
    for b in by_band.values():
        b["escape_rate"] = round(b["defective_prs"] / b["prs"], 3) if b["prs"] else None
    bad, good = [u for u in prs if u["defects"]], [u for u in prs if not u["defects"]]
    aspect_auc, aspect_means = {}, {}
    for key in SCORE_KEYS:
        sp, sn = [_aspect(u, key) for u in bad], [_aspect(u, key) for u in good]
        aspect_auc[key] = auc(sp, sn)
        aspect_means[key] = {"defective": round(sum(sp) / len(sp), 1) if sp else None,
                             "clean": round(sum(sn) / len(sn), 1) if sn else None}
    bands = [b for b in BAND_NAMES if b in by_band]
    return {
        "auc_max_score": auc([u["max_score"] for u in bad], [u["max_score"] for u in good]),
        "auc_note": "0.5 = the overall score is no better than random at predicting escaped defects; 1.0 = perfect",
        "by_band": {b: by_band[b] for b in bands},
        "band_mix": {b: round(by_band[b]["prs"] / len(prs), 3) for b in bands} if prs else {},
        "aspect_auc": aspect_auc, "aspect_means": aspect_means,
    }


def summarise(u, mods):
    """Copy of PR `u` touching `mods`: its band is the most severe one, its score the highest (an unrated module counts
    at the floor of its band)."""
    u = dict(u, modules=mods)
    u["max_tier"] = max([m["band"] for m in mods] or ["Low"], key=severity)
    scores = [m["score"] for m in mods if m["score"] is not None] + [band_floor(m["band"]) for m in mods if m["score"] is None]
    u["max_score"] = max(scores, default=0)
    return u


def rescore(prs, policy):
    """Copy of `prs` with every rated module re-scored from its facts under `policy`."""
    out = []
    for u in prs:
        mods = []
        for m in u["modules"]:
            if m.get("rated") and m.get("facts"):
                s = rescore_module(m["facts"], policy)
                mods.append({**m, "scores": s, "score": s["overall"], "band": band(s["overall"])})
            else:
                mods.append({**m, "band": policy.unrated_band, "score": None})
        out.append(summarise(u, mods))
    return out


def replay(history, policy):
    """Calibration of `policy` on an already-collected history (weights, mixes and thresholds; not module shape)."""
    cal = calibration(rescore(history["prs"], policy))
    return {k: cal[k] for k in ("auc_max_score", "by_band", "band_mix")}


def sensitivity(history, policy, step=0.1):
    """One-knob-at-a-time replays: the overall peak share and every weight of the four aspects, each ±step and -> 0."""
    rows = [{"change": "baseline", **replay(history, policy)}]
    for nv in (policy.overall_peak_share + step, policy.overall_peak_share - step):
        if 0 <= nv <= 1:
            rows.append({"change": "overall_peak_share %g→%g" % (policy.overall_peak_share, round(nv, 3)),
                         **replay(history, dataclasses.replace(policy, overall_peak_share=round(nv, 3)))})
    for group in SENSITIVITY_GROUPS:
        weights = getattr(policy, group)
        for key, w in weights.items():
            seen = {round(w, 3)}
            for label, nw in (("+%g" % step, w + step), ("-%g" % step, max(0.0, w - step)), ("=0", 0.0)):
                nw = round(nw, 3)
                if nw in seen or sum(weights.values()) - w + nw <= 0:
                    continue
                seen.add(nw)
                variant = dataclasses.replace(policy, **{group: {**weights, key: nw}})
                rows.append({"change": "%s.%s %g→%g (%s)" % (group, key, w, nw, label), **replay(history, variant)})
    return rows
