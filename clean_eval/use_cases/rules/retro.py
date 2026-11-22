"""The retro's rules: reading the merged-PR history (escaped defects, review signal, rule stats), calibrating the risk
model against it, and judging the tuning the agent proposes."""

import json
import re
from collections import (
    Counter,
    defaultdict,
)
from collections.abc import (
    Collection,
    Sequence,
)
from typing import (
    Optional,
    Union,
)

from pydantic import ValidationError

from clean_eval.domain.errors import (
    InvalidOutput,
    RemoteFailure,
)
from clean_eval.domain.policy import (
    WEIGHT_GROUPS,
    Aspect,
    Band,
    Policy,
)
from clean_eval.domain.rating import ModuleRecord
from clean_eval.domain.retro import (
    AgentRetroResult,
    AspectMeans,
    BandStats,
    Calibration,
    Confidence,
    DefectKind,
    DefectLink,
    FetchFailure,
    HostComment,
    MergedPR,
    ModelChange,
    ModuleStats,
    Period,
    PolicyEdit,
    Recommendation,
    Rejection,
    Replay,
    RetroHistory,
    RetroResult,
    ReviewSignal,
    ReviewThreads,
    RulesChange,
    RuleStats,
    RuleUse,
    SensitivityRow,
)
from clean_eval.domain.review import (
    Secrets,
    StepOutputs,
)
from clean_eval.use_cases.rules.analysis import ignored
from clean_eval.use_cases.rules.heatmap import (
    band,
    band_floor,
    severity,
)
from clean_eval.use_cases.rules.heatmap import rescore as rescore_module
from clean_eval.use_cases.rules.review import (
    REVIEW_MARKER,
    check_rules,
    redact,
)

# Merged-PR history: which later fixes and reverts point back at a merged PR ("escaped defects"), and what its
# clean-eval review cited. Each first-parent commit of the main branch is one merged PR (merge or squash), whose number
# the code host reads from its subject. The fix-to-PR links are heuristics (shared files plus fix/revert wording) that
# the retro skill verifies from the diffs.

RULE_ID = re.compile(r"\[([A-Z]{1,4}-\d{2,3}|NEW)\]")  # pr-review tags findings as [CA-01] / [NEW]
FIX = re.compile(r"(?i)\b(fix(es|ed)?|bug|hotfix|regression|patch|revert)\b")
REVERT = re.compile(r"(?i)^revert\b")
FETCH_ERROR_RATE_LIMIT = 0.2  # above this share of failed review fetches, rule metrics are flagged unreliable
DAY = 86400


def in_period(
    units: list[MergedPR],
    period: Period,
) -> list[MergedPR]:
    return [u for u in units if u.ts <= period.until_ts]


def attribute_defects(
    units: list[MergedPR],
    prs: list[MergedPR],
    period: Period,
    policy: Policy,
) -> list[MergedPR]:
    """Attach to each of `prs` the later fixes and reverts (from `units`, the period plus its look-ahead) that most
    likely repaired it: a fix or revert merged within the window that shares files with the PR. Of several such PRs, one
    the fix names (its short sha, or the reverted subject) wins, else the most recent; `confidence` says which."""
    window = period.window_days * DAY
    for u in prs:
        u.defects = []
    for fix in units:
        is_revert = bool(REVERT.search(fix.subject))
        if not (is_revert or FIX.search(fix.subject)):
            continue
        fset = {f for f in fix.files if not ignored(f, policy)}
        candidates = [u for u in prs if u.sha != fix.sha and 0 < fix.ts - u.ts <= window and fset & set(u.files)]
        if not candidates:
            continue
        strong = [u for u in candidates if u.sha[:7] in fix.subject or (is_revert and u.subject in fix.subject)]
        target = (strong or candidates)[-1]
        target.defects.append(
            DefectLink(
                fix_sha=fix.sha,
                fix_pr=fix.pr,
                subject=fix.subject,
                kind=DefectKind.REVERT if is_revert else DefectKind.FIX,
                confidence=Confidence.HIGH if strong else Confidence.HEURISTIC,
                days_after=round((fix.ts - target.ts) / DAY, 1),
                overlap_files=sorted(fset & set(target.files))[:20],
            )
        )
    return prs


def is_reviewer(
    comment: HostComment,
    reviewer_logins: Optional[Collection[str]] = None,
) -> bool:
    """Whose clean-eval markers count: the given logins', else any app's (an app's account can't be registered by a
    user, so a commenter can't pass as one)."""
    if not comment.author:
        return False
    return comment.author in reviewer_logins if reviewer_logins else comment.by_app


def review_signal(
    threads: ReviewThreads,
    reviewer_logins: Optional[Collection[str]] = None,
) -> ReviewSignal:
    """What one PR's reviews said, from what was said on it (its ReviewThreads)."""
    comments = threads.comments
    marker, marker_authors, ignored_markers = {}, set(), 0
    for comment in comments:
        for m in REVIEW_MARKER.finditer(comment.body):
            if not is_reviewer(comment, reviewer_logins):
                ignored_markers += 1  # a marker from anyone else is not trusted
                continue
            marker_authors.add(comment.author)
            for kv in m.group(1).split():
                k, _, v = kv.partition("=")
                marker[k] = v
    # When a clean-eval review exists, measure rules by what *it* cited, not by third-party comments.
    cited_text = "\n".join(c.body for c in comments if not marker_authors or c.author in marker_authors)
    tags = RULE_ID.findall(cited_text)
    return ReviewSignal(
        url=threads.url,
        labels=list(threads.labels),
        size=threads.size,
        review_comment_count=len([c for c in comments if c.body.strip()]),
        rules_cited=sorted(set(tags) - {"NEW"}),
        new_findings=tags.count("NEW"),
        clean_eval_marker=marker or None,
        untrusted_markers_ignored=ignored_markers,
    )


def rule_stats(
    prs: list[MergedPR],
) -> RuleStats:
    """How the review rules did on the PRs whose reviews were fetched; unreliable when too many fetches failed."""
    fetched = [(u, u.review) for u in prs if isinstance(u.review, ReviewSignal)]
    failures = [(u.pr, u.review.error) for u in prs if u.pr is not None and isinstance(u.review, FetchFailure)]
    failed = [pr for pr, error in failures if error == "error"]
    not_found = [pr for pr, error in failures if error == RemoteFailure.NOT_FOUND]
    attempted = len(fetched) + len(failed)
    error_rate = round(len(failed) / attempted, 3) if attempted else 0.0
    per_rule = defaultdict(RuleUse)
    for u, review in fetched:
        for r in review.rules_cited:
            per_rule[r].prs_cited += 1
            per_rule[r].cited_in_defective_prs += bool(u.defects)
    return RuleStats(
        per_rule=dict(per_rule),
        prs_reviewed=len(fetched),
        prs_fetch_failed=failed,
        prs_not_found=not_found,
        gh_error_rate=error_rate,
        reliable=error_rate <= FETCH_ERROR_RATE_LIMIT,
        prs_with_clean_eval_review=sum(1 for _, review in fetched if review.clean_eval_marker),
        new_findings=sum(review.new_findings for _, review in fetched),
        defective_prs_with_no_rule_cited=[
            u.pr for u, review in fetched if u.pr is not None and u.defects and not review.rules_cited
        ],
    )


def rules_warning(
    rules: RuleStats,
) -> Optional[str]:
    """A warning when some review fetches failed, or None."""
    failed = len(rules.prs_fetch_failed)
    if not failed:
        return None
    return "review fetch failed for %d/%d PRs (%d%%)%s" % (
        failed,
        rules.prs_reviewed + failed,
        round(rules.gh_error_rate * 100),
        "" if rules.reliable else " - rule metrics unreliable",
    )


def module_stats(
    prs: list[MergedPR],
) -> dict[str, ModuleStats]:
    """Per module, most escaped defects first: PRs that touched it, how many had defects, mean score, bands seen."""
    touched, defective = Counter(), Counter()
    scores, bands = defaultdict(list), defaultdict(Counter)
    for u in prs:
        for m in u.modules:
            touched[m.module] += 1
            defective[m.module] += bool(u.defects)
            if m.score is not None:
                scores[m.module].append(m.score)
            bands[m.module][m.band] += 1
    return {
        name: ModuleStats(
            prs=touched[name],
            defective_prs=defective[name],
            escape_rate=round(defective[name] / touched[name], 3),
            mean_score=round(sum(scores[name]) / len(scores[name]), 1) if scores[name] else None,
            bands_seen=dict(bands[name]),
        )
        for name in sorted(touched, key=lambda name: -defective[name])
    }


# How well the risk scores predicted escaped defects (AUC, escape rate per band, per aspect), and replays of a candidate
# policy on an already-collected history: every rated module is re-scored from its stored facts, so a change of weights,
# mixes or thresholds is evaluated in milliseconds without touching git. Module shape (depth, paths) needs a re-collect.


def auc(
    pos: Sequence[float],
    neg: Sequence[float],
) -> Optional[float]:
    """P(score of a defective PR > score of a clean PR); ties count half. None if undefined."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def aspect_peak(
    merged_pr: MergedPR,
    key: str,
) -> int:
    """Highest score of one aspect among the rated modules a PR touched (0 when none)."""
    return max([getattr(m.scores, key) or 0 for m in merged_pr.modules if m.rated and m.scores] or [0])


def calibration(
    prs: list[MergedPR],
) -> Calibration:
    by_band = defaultdict(BandStats)
    for u in prs:
        by_band[u.max_tier].prs += 1
        by_band[u.max_tier].defective_prs += bool(u.defects)
    for b in by_band.values():
        b.escape_rate = round(b.defective_prs / b.prs, 3) if b.prs else None
    bad, good = [u for u in prs if u.defects], [u for u in prs if not u.defects]
    aspect_auc, aspect_means = {}, {}
    for key in (aspect.score_key for aspect in Aspect):
        sp, sn = [aspect_peak(u, key) for u in bad], [aspect_peak(u, key) for u in good]
        aspect_auc[key] = auc(sp, sn)
        aspect_means[key] = AspectMeans(
            defective=round(sum(sp) / len(sp), 1) if sp else None,
            clean=round(sum(sn) / len(sn), 1) if sn else None,
        )
    bands = [b for b in Band if b in by_band]
    return Calibration(
        auc_max_score=auc([u.max_score for u in bad], [u.max_score for u in good]),
        by_band={b: by_band[b] for b in bands},
        band_mix={b: round(by_band[b].prs / len(prs), 3) for b in bands} if prs else {},
        aspect_auc=aspect_auc,
        aspect_means=aspect_means,
    )


def summarise(
    merged_pr: MergedPR,
    mods: list[ModuleRecord],
) -> MergedPR:
    """Copy of `merged_pr` touching `mods`: its band is the most severe one, its score the highest (an unrated module
    counts at the floor of its band)."""
    scores = [m.score for m in mods if m.score is not None] + [band_floor(m.band) for m in mods if m.score is None]
    return merged_pr.model_copy(
        update={
            "modules": mods,
            "max_tier": max([m.band for m in mods] or [Band.LOW], key=severity),
            "max_score": max(scores, default=0),
        }
    )


def rescore(
    prs: list[MergedPR],
    policy: Policy,
) -> list[MergedPR]:
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


def replay(
    history: RetroHistory,
    policy: Policy,
) -> Replay:
    """Calibration of `policy` on an already-collected RetroHistory (weights, mixes and thresholds; not module shape)."""
    cal = calibration(rescore(history.prs, policy))
    return Replay(
        auc_max_score=cal.auc_max_score,
        by_band=cal.by_band,
        band_mix=cal.band_mix,
    )


def sensitivity(
    history: RetroHistory,
    policy: Policy,
    step: float = 0.1,
) -> list[SensitivityRow]:
    """One-knob-at-a-time replays: the overall peak share and every weight of the four aspects, each ±step and -> 0."""
    rows = [
        SensitivityRow(
            change="baseline",
            **replay(history, policy).model_dump(),
        )
    ]
    for nv in (policy.overall_peak_share + step, policy.overall_peak_share - step):
        if 0 <= nv <= 1:
            variant = policy.model_copy(update={"overall_peak_share": round(nv, 3)})
            rows.append(
                SensitivityRow(
                    change="overall_peak_share %g→%g" % (policy.overall_peak_share, round(nv, 3)),
                    **replay(history, variant).model_dump(),
                )
            )
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
                rows.append(
                    SensitivityRow(
                        change="%s.%s %g→%g (%s)" % (group, key, w, nw, label),
                        **replay(history, variant).model_dump(),
                    )
                )
    return rows


# Which edits of the tuning files (`.clean-eval/risk-model.json` and the rule packs in `.clean-eval/rules/`) an unattended
# retro may keep.
#
# The domain models check the shape of an edit (a valid policy changing only tunable knobs; rules that parse into unique
# rows). The evidence is judged here: enough PRs in the period, and a replay AUC no lower than the current model's.

MODEL_FILE, RULE_PACKS = "risk-model.json", "rules"  # in .clean-eval/
MIN_PRS, MIN_DEFECTIVE = 20, 5  # below this, a model edit has no evidence behind it


def judge_model_edit(
    new_text: Optional[str],
    orig_text: Optional[str],
    history: RetroHistory,
) -> ModelChange:
    """The accepted change of the risk-model overrides (None text: no file, i.e. the defaults), judged on the collected
    RetroHistory; InvalidOutput (or a parse error) when it has to be restored."""
    try:
        edit = PolicyEdit(
            new=json.loads(new_text) if new_text is not None else {},
            orig=json.loads(orig_text) if orig_text is not None else {},
        )
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, MODEL_FILE)
    prs, defective = history.totals.prs, history.totals.defective_prs
    if prs < MIN_PRS or defective < MIN_DEFECTIVE:
        raise InvalidOutput(
            "sample too small for a model change (%d PRs, %d defective; need at least %d and %d)"
            % (prs, defective, MIN_PRS, MIN_DEFECTIVE)
        )
    # compare replay with replay: stored signals are rounded, so a replay of the unchanged model can differ slightly
    # from the collected AUC
    before = replay(history, Policy.from_overrides(edit.orig)).auc_max_score
    after = replay(history, Policy.from_overrides(edit.new)).auc_max_score
    if before is None or after is None:
        raise InvalidOutput(
            "AUC is undefined for this period (no defective or no clean PRs): no evidence for a change",
        )
    if after < before:
        raise InvalidOutput(
            "replay AUC %s is below the current model's %s" % (after, before),
        )
    return ModelChange(
        changed_keys=sorted(edit.changed_keys),
        auc_before=before,
        auc_after=after,
    )


def judge_rules_edit(
    rules: str,
    packs: list[str],
) -> RulesChange:
    """The accepted change of the rule packs `packs`, by the `rules` all packs make together; InvalidOutput when those
    don't parse into unique rule rows."""
    try:
        check_rules(rules)
    except ValidationError as e:
        raise InvalidOutput.from_validation(e)
    return RulesChange(
        packs=packs,
    )


def retro_recommendations(
    raw: object,
    secrets: Secrets,
) -> list[Recommendation]:
    """The recommendations of the agent's result.json, validated and redacted of `secrets`."""
    try:
        recs = AgentRetroResult.model_validate(raw).recommendations
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, "result.json")
    return [
        r.model_copy(update={k: redact(getattr(r, k), secrets)[0] for k in ("target", "change", "evidence")})
        for r in recs
    ]


def retro_result(
    history: RetroHistory,
    recommendations: list[Recommendation],
    model_change: Optional[Union[ModelChange, Rejection]],
    rules_change: Optional[Union[RulesChange, Rejection]],
    other_reverted: list[str],
) -> tuple[RetroResult, StepOutputs]:
    """(RetroResult, step outputs). Counts and AUC come from the collected history, never from agent text."""
    baseline = history.calibration.auc_max_score
    res = RetroResult(
        since=history.period.since,
        until=history.period.until,
        prs=history.totals.prs,
        defective_prs=history.totals.defective_prs,
        auc=baseline,
        recommendations=recommendations,
        model_change=model_change,
        rules_change=rules_change,
        other_files_reverted=other_reverted,
    )
    return res, StepOutputs(
        {"auc": "" if baseline is None else "%.3f" % baseline, "applied": "true" if res.applied else "false"}
    )
