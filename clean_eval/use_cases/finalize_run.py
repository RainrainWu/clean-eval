"""Use case: turn what the CI agent proposed (its report.md and result.json) into what CI trusts.

  pr-review       validate the findings: unknown rule IDs become NEW, a severity below its rule's is raised to it, and the
                  verdict, blocker count, band and score are derived from the findings and the precomputed risk
  eval            keep an edit of the tuning files only with apply on and only if it passes domain/tuning.py; restore
                  everything else, and every edit when the agent's output is invalid
  risk-heat-map   the report is the deterministic summary (the agent only levels)

Reports and results are redacted; the step outputs carry only enums and numbers, never agent text."""
from ..domain.errors import HeatmapError, InvalidOutput, InvalidRequest
from ..domain.review import finalize_review, parse_rules, review_outputs
from ..domain.tuning import (MODEL_FILE, RULES_FILE, TUNING_FILES, eval_recommendations, eval_result, judge_model_edit,
                             judge_rules_edit)

SKILLS = ("pr-review", "eval", "risk-heat-map")
# What judging a malformed agent output (or edit) can raise: it then counts as invalid and is restored, never a crash.
JUDGING_ERRORS = (HeatmapError, ArithmeticError, ValueError, KeyError, TypeError, AttributeError)


def _invalid(e, suffix=""):
    return InvalidOutput((str(e) if isinstance(e, HeatmapError) else "%s: %s" % (type(e).__name__, e)) + suffix)


class FinalizeRun:
    def __init__(self, ctx, agent, out, redactor, tuning=None):
        self.ctx, self.agent, self.out, self.redactor, self.tuning = ctx, agent, out, redactor, tuning

    def execute(self, skill, apply=False):
        """(result, step outputs), with result.json (None for the heat map) and report.md written to the output folder.
        InvalidOutput when the agent's output can't be trusted; nothing is written then."""
        if skill == "pr-review":
            res, outputs = self._review()
        elif skill == "eval":
            res, outputs = self._eval(apply)
        elif skill == "risk-heat-map":
            res, outputs = None, {}
        else:
            raise InvalidRequest("unknown skill %r (expected %s)" % (skill, " | ".join(SKILLS)))
        folder, name = (self.out, "heatmap.md") if skill == "risk-heat-map" else (self.agent, "report.md")
        report, n = self.redactor.sanitize_report(folder.read_text(name) if folder.exists(name) else "_no report written_")
        self.out.write_text("report.md", report)
        if res is not None:
            res["redactions"] = res.get("redactions", 0) + n
            self.out.write_json("result.json", res)
        return res, outputs

    def _review(self):
        try:
            res = finalize_review(self.agent.read_json("result.json"), self.ctx.read_json("risk.json"),
                                  parse_rules(self.ctx.read_text("review-rules.md")), self.redactor)
        except JUDGING_ERRORS as e:
            raise _invalid(e)
        return res, review_outputs(res)

    def _eval(self, apply):
        """Judge the edits before the agent's output, so an invalid output never leaves an unchecked edit behind; when
        anything fails, restore every tuning file."""
        try:
            history = self.ctx.read_json("eval-history.json")
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
        """The agent's change of one tuning file: kept when `judge` accepts it, else restored. None when unchanged."""
        new, orig = self.tuning.current(name), self.tuning.original(name)
        if new == orig:
            return None
        try:
            if not apply:
                raise InvalidOutput("apply=false")
            return judge(new, orig)
        except JUDGING_ERRORS as e:
            self.tuning.restore(name)
            return {"accepted": False, "reason": str(e)}
