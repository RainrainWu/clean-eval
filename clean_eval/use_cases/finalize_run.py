"""Use case: turn what the CI agent proposed (its report.md and result.json in the agent folder) into what CI trusts, in
the output folder. Through the FileSystem and Environment interfaces (the environment names the secrets to redact).

  pr-review       the domain models check the findings' shape; the review rules hold them to the rules table and
                  derive the verdict, blocker count, band and score (domain/review.py, rules/review.py)
  eval            keep an edit of the tuning files only with apply on and only if it passes the checks of
                  domain/tuning.py and rules/tuning.py; restore everything else, and every edit when the output is invalid
  risk-heat-map   the report is the deterministic summary (the agent only levels)

Reports and results are redacted; the step outputs carry only enums and numbers, never agent text."""

from pydantic import ValidationError

from clean_eval.domain.ci import StepOutputs
from clean_eval.domain.errors import HeatmapError, InvalidOutput, InvalidRequest
from clean_eval.domain.history import EvalHistory
from clean_eval.domain.review import AgentReview, RiskReport
from clean_eval.domain.tuning import Rejection
from clean_eval.use_cases.rules.redaction import sanitize_report, secret_values
from clean_eval.use_cases.rules.review import finalize_review, parse_rules, review_outputs
from clean_eval.use_cases.rules.tuning import (
    MODEL_FILE,
    RULES_FILE,
    TUNING_FILES,
    eval_recommendations,
    eval_result,
    judge_model_edit,
    judge_rules_edit,
)
from clean_eval.use_cases.store import read_json, write_json
from clean_eval.use_cases.tuning_files import (
    SNAPSHOT,
    current_tuning,
    original_tuning,
    restore_other_tuning,
    restore_tuning,
)

SKILLS = ("pr-review", "eval", "risk-heat-map")
# What judging a malformed agent output (or edit) can raise: it then counts as invalid and is restored, never a crash.
JUDGING_ERRORS = (HeatmapError, ArithmeticError, ValueError, KeyError, TypeError, AttributeError)


def _invalid(e, suffix=""):
    if isinstance(e, ValidationError):
        return InvalidOutput(str(InvalidOutput.from_validation(e)) + suffix)
    return InvalidOutput((str(e) if isinstance(e, HeatmapError) else "%s: %s" % (type(e).__name__, e)) + suffix)


def _validated(model, fs, folder, name):
    """`name` in `folder`, validated as `model`; InvalidOutput says which file and what is wrong."""
    try:
        return model.model_validate(read_json(fs, fs.join(folder, name)))
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, name)


def finalize_run(fs, env, ctx, agent, out, skill, apply=False, workspace="."):
    """(result, StepOutputs), with result.json (None for the heat map) and report.md written to `out`. InvalidOutput when
    the agent's output can't be trusted; nothing is written then."""
    secrets = secret_values(env.variables())
    if skill == "pr-review":
        res, outputs = _review(fs, ctx, agent, secrets)
    elif skill == "eval":
        res, outputs = _eval(fs, ctx, agent, apply, workspace, secrets)
    elif skill == "risk-heat-map":
        res, outputs = None, StepOutputs({})
    else:
        raise InvalidRequest("unknown skill %r (expected %s)" % (skill, " | ".join(SKILLS)))
    report_path = fs.join(out, "heatmap.md") if skill == "risk-heat-map" else fs.join(agent, "report.md")
    report, n = sanitize_report(fs.read_text(report_path) if fs.exists(report_path) else "_no report written_", secrets)
    fs.write_text(fs.join(out, "report.md"), report)
    if res is not None:
        res.redactions += n
        write_json(fs, fs.join(out, "result.json"), res)
    return res, outputs


def _review(fs, ctx, agent, secrets):
    try:
        review = finalize_review(
            _validated(AgentReview, fs, agent, "result.json"),
            _validated(RiskReport, fs, ctx, "risk.json"),
            parse_rules(fs.read_text(fs.join(ctx, "review-rules.md"))),
            secrets,
        )
    except JUDGING_ERRORS as e:
        raise _invalid(e)
    return review, review_outputs(review)


def _eval(fs, ctx, agent, apply, workspace, secrets):
    """Judge the edits before the agent's output, so an invalid output never leaves an unchecked edit behind; when
    anything fails, restore every tuning file."""
    snapshot_dir = fs.join(ctx, SNAPSHOT)
    try:
        history = _validated(EvalHistory, fs, ctx, "eval-history.json")
        model_change = _judge(
            fs, workspace, snapshot_dir, MODEL_FILE, apply, lambda new, orig: judge_model_edit(new, orig, history)
        )
        rules_change = _judge(fs, workspace, snapshot_dir, RULES_FILE, apply, lambda new, orig: judge_rules_edit(new))
        other = restore_other_tuning(fs, workspace, snapshot_dir, TUNING_FILES)
        recommendations = eval_recommendations(read_json(fs, fs.join(agent, "result.json")), secrets)
        return eval_result(history, recommendations, model_change, rules_change, other)
    except JUDGING_ERRORS as e:
        for name in sorted(TUNING_FILES):
            restore_tuning(fs, workspace, snapshot_dir, name)
        restore_other_tuning(fs, workspace, snapshot_dir, TUNING_FILES)
        raise _invalid(e, " (any .clean-eval/ edits were reverted)")


def _judge(fs, workspace, snapshot_dir, name, apply, judge):
    """The agent's change of one tuning file: kept when `judge` accepts it, else restored (a Rejection says why). None
    when the file is unchanged."""
    new, orig = current_tuning(fs, workspace, name), original_tuning(fs, snapshot_dir, name)
    if new == orig:
        return None
    try:
        if not apply:
            raise InvalidOutput("apply=false")
        return judge(new, orig)
    except JUDGING_ERRORS as e:
        restore_tuning(fs, workspace, snapshot_dir, name)
        return Rejection(reason=str(_invalid(e)) if isinstance(e, ValidationError) else str(e))
