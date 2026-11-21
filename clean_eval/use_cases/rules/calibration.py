"""How well the risk scores predicted escaped defects (AUC, escape rate per band, per aspect), and replays of a candidate
policy on an already-collected history: every rated module is re-scored from its stored facts, so a change of weights,
mixes or thresholds is evaluated in milliseconds without touching git. Module shape (depth, paths) needs a re-collect."""
from collections import defaultdict

from ...domain.history import AspectMeans, BandStats, Calibration, Replay, SensitivityRow
from ...domain.policy import SCORE_KEYS, WEIGHT_GROUPS
from ...domain.values import BAND_NAMES
from .rating import rescore as rescore_module
from .scoring import band, band_floor, severity


def auc(pos, neg):
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def _aspect(u, key):
    """Highest score of one aspect among the rated modules a PR touched (0 when none)."""
    return max([getattr(m.scores, key) or 0 for m in u.modules if m.rated and m.scores] or [0])


def calibration(prs):
    by_band = defaultdict(BandStats)
    for u in prs:
        by_band[u.max_tier].prs += 1
        by_band[u.max_tier].defective_prs += bool(u.defects)
    for b in by_band.values():
        b.escape_rate = round(b.defective_prs / b.prs, 3) if b.prs else None
    bad, good = [u for u in prs if u.defects], [u for u in prs if not u.defects]
    aspect_auc, aspect_means = {}, {}
    for key in SCORE_KEYS:
        sp, sn = [_aspect(u, key) for u in bad], [_aspect(u, key) for u in good]
        aspect_auc[key] = auc(sp, sn)
        aspect_means[key] = AspectMeans(defective=round(sum(sp) / len(sp), 1) if sp else None,
                                        clean=round(sum(sn) / len(sn), 1) if sn else None)
    bands = [b for b in BAND_NAMES if b in by_band]
    return Calibration(auc_max_score=auc([u.max_score for u in bad], [u.max_score for u in good]),
                       by_band={b: by_band[b] for b in bands},
                       band_mix={b: round(by_band[b].prs / len(prs), 3) for b in bands} if prs else {},
                       aspect_auc=aspect_auc, aspect_means=aspect_means)


def summarise(u, mods):
    """Copy of PR `u` touching `mods`: its band is the most severe one, its score the highest (an unrated module counts
    at the floor of its band)."""
    scores = [m.score for m in mods if m.score is not None] + [band_floor(m.band) for m in mods if m.score is None]
    return u.model_copy(update={"modules": mods, "max_tier": max([m.band for m in mods] or ["Low"], key=severity),
                                "max_score": max(scores, default=0)})


def rescore(prs, policy):
    """Copy of `prs` with every rated module re-scored from its facts under `policy`."""
    out = []
    for u in prs:
        mods = []
        for m in u.modules:
            if m.rated and m.facts is not None:
                s = rescore_module(m.facts, policy)
                mods.append(m.model_copy(update={"scores": s, "score": s.overall, "band": band(s.overall)}))
            elif m.rated:
                mods.append(m)  # stored without facts: nothing to re-score, its rating stands
            else:
                mods.append(m.model_copy(update={"band": policy.unrated_band}))
        out.append(summarise(u, mods))
    return out


def replay(history, policy):
    """Calibration of `policy` on an already-collected EvalHistory (weights, mixes and thresholds; not module shape)."""
    cal = calibration(rescore(history.prs, policy))
    return Replay(auc_max_score=cal.auc_max_score, by_band=cal.by_band, band_mix=cal.band_mix)


def sensitivity(history, policy, step=0.1):
    """One-knob-at-a-time replays: the overall peak share and every weight of the four aspects, each ±step and -> 0."""
    rows = [SensitivityRow(change="baseline", **replay(history, policy).model_dump())]
    for nv in (policy.overall_peak_share + step, policy.overall_peak_share - step):
        if 0 <= nv <= 1:
            variant = policy.model_copy(update={"overall_peak_share": round(nv, 3)})
            rows.append(SensitivityRow(change="overall_peak_share %g→%g" % (policy.overall_peak_share, round(nv, 3)),
                                       **replay(history, variant).model_dump()))
    for group in WEIGHT_GROUPS:
        weights = getattr(policy, group)
        for key, w in weights.items():
            seen = {round(w, 3)}
            for label, nw in (("+%g" % step, w + step), ("-%g" % step, max(0.0, w - step)), ("=0", 0.0)):
                nw = round(nw, 3)
                if nw in seen or sum(weights.values()) - w + nw <= 0:
                    continue
                seen.add(nw)
                variant = policy.model_copy(update={group: {**weights, key: nw}})
                rows.append(SensitivityRow(change="%s.%s %g→%g (%s)" % (group, key, w, nw, label),
                                           **replay(history, variant).model_dump()))
    return rows
