"""Which edits of the tuning files (`.clean-eval/risk-model.json` and `review-rules.md`) an unattended eval may keep.

A model edit must be a valid policy that changes only tunable knobs (weights, mixes, saturations, points, standards; never
what is measured), on a large enough sample, without lowering AUC on replay. A rules edit must still parse into unique
rule rows. Whatever fails is restored; so is any other file the agent touched."""
import json

from .calibration import replay
from .errors import InvalidOutput, InvalidPolicy
from .policy import TUNABLE, Policy
from .review import check_rules, text_field

MODEL_FILE, RULES_FILE = "risk-model.json", "review-rules.md"  # in .clean-eval/
TUNING_FILES = frozenset({MODEL_FILE, RULES_FILE})
MIN_PRS, MIN_DEFECTIVE = 20, 5  # below this, a model edit has no evidence behind it
MAX_RECOMMENDATIONS = 30


def _comment(key):
    return key.startswith("_") or key == "notes"


def check_model_change(new, orig):
    """The keys the risk-model overrides `new` change from `orig`; InvalidOutput unless `new` is a valid policy that
    differs only in tunable knobs, never in what is measured (module shape, path conventions)."""
    if not isinstance(new, dict):
        raise InvalidOutput("risk-model.json must be an object")
    changed = {k for k in set(new) | set(orig) if not _comment(k) and new.get(k) != orig.get(k)}
    structural = changed - TUNABLE - {"version"}
    if structural:
        raise InvalidOutput("only tunable keys may change in CI; also changed: %s" % sorted(structural))
    try:
        Policy.from_overrides(new)
    except InvalidPolicy as e:
        raise InvalidOutput(str(e))
    return changed


def judge_model_edit(new_text, orig_text, history):
    """The accepted change of the risk-model overrides (None text: no file, i.e. the defaults), judged on the collected
    history; InvalidOutput (or a parse error) when it has to be restored."""
    new = json.loads(new_text) if new_text is not None else {}
    orig = json.loads(orig_text) if orig_text is not None else {}
    changed = check_model_change(new, orig)
    prs, defective = history["totals"]["prs"], history["totals"]["defective_prs"]
    if prs < MIN_PRS or defective < MIN_DEFECTIVE:
        raise InvalidOutput("sample too small for a model change (%d PRs, %d defective; need at least %d and %d)"
                            % (prs, defective, MIN_PRS, MIN_DEFECTIVE))
    # compare replay with replay: stored signals are rounded, so a replay of the unchanged model can differ slightly
    # from the collected AUC
    before = replay(history, Policy.from_overrides(orig))["auc_max_score"]
    after = replay(history, Policy.from_overrides(new))["auc_max_score"]
    if before is None or after is None:
        raise InvalidOutput("AUC is undefined for this period (no defective or no clean PRs): no evidence for a change")
    if after < before:
        raise InvalidOutput("replay AUC %s is below the current model's %s" % (after, before))
    return {"accepted": True, "changed_keys": sorted(changed), "auc_before": before, "auc_after": after}


def judge_rules_edit(new_text):
    """The accepted change of the review rules; InvalidOutput when they are removed or don't parse into unique rows."""
    if new_text is None:
        raise InvalidOutput("review-rules.md may not be removed")
    check_rules(new_text)
    return {"accepted": True}


def eval_recommendations(raw, redactor):
    """The recommendations of the agent's result.json, as redacted strings."""
    if not isinstance(raw, dict):
        raise InvalidOutput("result.json must be an object")
    recs = raw.get("recommendations") or []
    if not isinstance(recs, list):
        raise InvalidOutput("recommendations must be a list")
    return [{k: redactor.redact(text_field(str(x.get(k, "")), "recommendations." + k, 2000))[0]
             for k in ("target", "change", "evidence")} for x in recs[:MAX_RECOMMENDATIONS] if isinstance(x, dict)]


def eval_result(history, recommendations, model_change, rules_change, other_reverted):
    """(result, step outputs) of an eval. Counts and AUC come from the collected history, never from agent text."""
    baseline = history["calibration"]["auc_max_score"]
    res = {"since": history["period"]["since"], "until": history["period"]["until"],
           "prs": history["totals"]["prs"], "defective_prs": history["totals"]["defective_prs"], "auc": baseline,
           "recommendations": recommendations, "model_change": model_change, "rules_change": rules_change,
           "other_files_reverted": other_reverted}
    res["applied"] = any((c or {}).get("accepted") for c in (model_change, rules_change))
    return res, {"auc": "" if baseline is None else "%.3f" % baseline, "applied": "true" if res["applied"] else "false"}
