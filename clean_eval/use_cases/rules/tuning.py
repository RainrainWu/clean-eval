"""Which edits of the tuning files (`.clean-eval/risk-model.json` and `review-rules.md`) an unattended eval may keep.

The domain models check the shape of an edit (a valid policy changing only tunable knobs; rules that parse into unique
rows). The evidence is judged here: enough PRs in the period, and a replay AUC no lower than the current model's."""
import json

from pydantic import ValidationError

from ...domain.ci import StepOutputs
from ...domain.errors import InvalidOutput
from ...domain.policy import Policy
from ...domain.tuning import AgentEvalResult, EvalResult, ModelChange, PolicyEdit, RulesChange
from .calibration import replay
from .review import check_rules

MODEL_FILE, RULES_FILE = "risk-model.json", "review-rules.md"  # in .clean-eval/
TUNING_FILES = frozenset({MODEL_FILE, RULES_FILE})
MIN_PRS, MIN_DEFECTIVE = 20, 5  # below this, a model edit has no evidence behind it


def judge_model_edit(new_text, orig_text, history):
    """The accepted change of the risk-model overrides (None text: no file, i.e. the defaults), judged on the collected
    EvalHistory; InvalidOutput (or a parse error) when it has to be restored."""
    try:
        edit = PolicyEdit(new=json.loads(new_text) if new_text is not None else {},
                          orig=json.loads(orig_text) if orig_text is not None else {})
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, MODEL_FILE)
    prs, defective = history.totals.prs, history.totals.defective_prs
    if prs < MIN_PRS or defective < MIN_DEFECTIVE:
        raise InvalidOutput("sample too small for a model change (%d PRs, %d defective; need at least %d and %d)"
                            % (prs, defective, MIN_PRS, MIN_DEFECTIVE))
    # compare replay with replay: stored signals are rounded, so a replay of the unchanged model can differ slightly
    # from the collected AUC
    before = replay(history, Policy.from_overrides(edit.orig)).auc_max_score
    after = replay(history, Policy.from_overrides(edit.new)).auc_max_score
    if before is None or after is None:
        raise InvalidOutput("AUC is undefined for this period (no defective or no clean PRs): no evidence for a change")
    if after < before:
        raise InvalidOutput("replay AUC %s is below the current model's %s" % (after, before))
    return ModelChange(changed_keys=sorted(edit.changed_keys), auc_before=before, auc_after=after)


def judge_rules_edit(new_text):
    """The accepted change of the review rules; InvalidOutput when they are removed or don't parse into unique rows."""
    if new_text is None:
        raise InvalidOutput("review-rules.md may not be removed")
    try:
        check_rules(new_text)
    except ValidationError as e:
        raise InvalidOutput.from_validation(e)
    return RulesChange()


def eval_recommendations(raw, redactor):
    """The recommendations of the agent's result.json, validated and redacted."""
    try:
        recs = AgentEvalResult.model_validate(raw).recommendations
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, "result.json")
    return [r.model_copy(update={k: redactor.redact(getattr(r, k))[0] for k in ("target", "change", "evidence")})
            for r in recs]


def eval_result(history, recommendations, model_change, rules_change, other_reverted):
    """(EvalResult, step outputs). Counts and AUC come from the collected history, never from agent text."""
    baseline = history.calibration.auc_max_score
    res = EvalResult(since=history.period.since, until=history.period.until, prs=history.totals.prs,
                     defective_prs=history.totals.defective_prs, auc=baseline, recommendations=recommendations,
                     model_change=model_change, rules_change=rules_change, other_files_reverted=other_reverted)
    return res, StepOutputs({"auc": "" if baseline is None else "%.3f" % baseline,
                             "applied": "true" if res.applied else "false"})
