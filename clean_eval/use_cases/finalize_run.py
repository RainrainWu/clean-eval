"""Use case: turn what the CI agent proposed (its report.md and result.json) into what CI trusts.

  pr-review       the domain models check the findings' shape; the review rules hold them to the rules table and
                  derive the verdict, blocker count, band and score (domain/review.py, rules/review.py)
  eval            keep an edit of the tuning files only with apply on and only if it passes the checks of
                  domain/tuning.py and rules/tuning.py; restore everything else, and every edit when the output is invalid
  risk-heat-map   the report is the deterministic summary (the agent only levels)

Reports and results are redacted; the step outputs carry only enums and numbers, never agent text."""
from pydantic import ValidationError

from ..domain.ci import StepOutputs
from ..domain.errors import HeatmapError, InvalidOutput, InvalidRequest
from ..domain.history import EvalHistory
from ..domain.review import AgentReview, RiskReport
from ..domain.tuning import Rejection
from .rules.review import finalize_review, parse_rules, review_outputs
from .rules.tuning import (MODEL_FILE, RULES_FILE, TUNING_FILES, eval_recommendations, eval_result, judge_model_edit,
                           judge_rules_edit)

SKILLS = ("pr-review", "eval", "risk-heat-map")
# What judging a malformed agent output (or edit) can raise: it then counts as invalid and is restored, never a crash.
JUDGING_ERRORS = (HeatmapError, ArithmeticError, ValueError, KeyError, TypeError, AttributeError)


def _invalid(e, suffix=""):
    if isinstance(e, ValidationError):
        return InvalidOutput(str(InvalidOutput.from_validation(e)) + suffix)
    return InvalidOutput((str(e) if isinstance(e, HeatmapError) else "%s: %s" % (type(e).__name__, e)) + suffix)


def _validated(model, folder, name):
    """`name` in `folder`, validated as `model`; InvalidOutput says which file and what is wrong."""
    try:
        return model.model_validate(folder.read_json(name))
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, name)


class FinalizeRun:
    def __init__(self, ctx, agent, out, redactor, tuning=None):
        self.ctx, self.agent, self.out, self.redactor, self.tuning = ctx, agent, out, redactor, tuning

    def execute(self, skill, apply=False):
        """(result, StepOutputs), with result.json (None for the heat map) and report.md written to the output folder.
        InvalidOutput when the agent's output can't be trusted; nothing is written then."""
        if skill == "pr-review":
            res, outputs = self._review()
        elif skill == "eval":
            res, outputs = self._eval(apply)
        elif skill == "risk-heat-map":
            res, outputs = None, StepOutputs({})
        else:
            raise InvalidRequest("unknown skill %r (expected %s)" % (skill, " | ".join(SKILLS)))
        folder, name = (self.out, "heatmap.md") if skill == "risk-heat-map" else (self.agent, "report.md")
        report, n = self.redactor.sanitize_report(folder.read_text(name) if folder.exists(name) else "_no report written_")
        self.out.write_text("report.md", report)
        if res is not None:
            res.redactions += n
            self.out.write_json("result.json", res)
        return res, outputs

    def _review(self):
        try:
            review = finalize_review(_validated(AgentReview, self.agent, "result.json"),
                                     _validated(RiskReport, self.ctx, "risk.json"),
                                     parse_rules(self.ctx.read_text("review-rules.md")), self.redactor)
        except JUDGING_ERRORS as e:
            raise _invalid(e)
        return review, review_outputs(review)

    def _eval(self, apply):
        """Judge the edits before the agent's output, so an invalid output never leaves an unchecked edit behind; when
        anything fails, restore every tuning file."""
        try:
            history = _validated(EvalHistory, self.ctx, "eval-history.json")
            model_change = self._judge(MODEL_FILE, apply, lambda new, orig: judge_model_edit(new, orig, history))
            rules_change = self._judge(RULES_FILE, apply, lambda new, orig: judge_rules_edit(new))
            other = self.tuning.restore_others(TUNING_FILES)
            recommendations = eval_recommendations(self.agent.read_json("result.json"), self.redactor)
            return eval_result(history, recommendations, model_change, rules_change, other)
        except JUDGING_ERRORS as e:
            for name in sorted(TUNING_FILES):
                self.tuning.restore(name)
            self.tuning.restore_others(TUNING_FILES)
            raise _invalid(e, " (any .clean-eval/ edits were reverted)")

    def _judge(self, name, apply, judge):
        """The agent's change of one tuning file: kept when `judge` accepts it, else restored (a Rejection says why).
        None when the file is unchanged."""
        new, orig = self.tuning.current(name), self.tuning.original(name)
        if new == orig:
            return None
        try:
            if not apply:
                raise InvalidOutput("apply=false")
            return judge(new, orig)
        except JUDGING_ERRORS as e:
            self.tuning.restore(name)
            return Rejection(reason=str(_invalid(e)) if isinstance(e, ValidationError) else str(e))
