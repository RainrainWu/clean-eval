"""The files of a run, through the FileSystem interface: JSON, the policy overrides, the rated heat map, a reviewer's
judgement, and GitHub step outputs."""

import json

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.domain.errors import HeatmapError, InvalidOutput
from clean_eval.domain.policy import REPO_POLICY, Policy
from clean_eval.domain.rating import Heatmap, Judgement
from clean_eval.use_cases.rules.review import sanitize_report


def read_json(fs, path):
    """The parsed JSON file; HeatmapError when it can't be read or parsed."""
    try:
        return json.loads(fs.read_text(path))
    except ValueError as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def write_json(fs, path, obj):
    """Write models (or plain data holding them) as JSON."""
    fs.write_text(path, json.dumps(to_jsonable_python(obj), indent=2))


def load_policy(fs, repo_root=None, path=None):
    """(Policy, source): `path`, else <repo>/.clean-eval/risk-model.json when present, else the defaults."""
    if path:
        return Policy.from_text(fs.read_text(path), path), path
    local = fs.join(repo_root or ".", REPO_POLICY)
    if fs.is_file(local) and not fs.is_link(local):
        return Policy.from_text(fs.read_text(local), REPO_POLICY), REPO_POLICY
    return Policy(), "clean-eval default"


def save_heatmap(fs, heatmap, path):
    fs.write_text(path, heatmap.model_dump_json(indent=1))


def load_heatmap(fs, path):
    try:
        return Heatmap.model_validate_json(fs.read_text(path))
    except ValidationError as e:
        raise HeatmapError.from_validation(e, "%s is not a heat map" % path)


def load_judgement(fs, path):
    """The levels in a judgement file; entries that don't validate are dropped (see Judgement)."""
    return Judgement.model_validate(read_json(fs, path))


def write_step_outputs(fs, path, outputs):
    """Append the StepOutputs (validated: one line each) as `name=value` lines to a GitHub Actions step-outputs file."""
    fs.append_text(path, "".join("%s=%s\n" % kv for kv in outputs.root.items()))


# What judging a malformed agent output (or edit) can raise: it then counts as invalid and is restored, never a crash.
JUDGING_ERRORS = (HeatmapError, ArithmeticError, ValueError, KeyError, TypeError, AttributeError)


def invalid(e, suffix=""):
    """The InvalidOutput that a failure to judge the CI agent's output becomes."""
    if isinstance(e, ValidationError):
        return InvalidOutput(str(InvalidOutput.from_validation(e)) + suffix)
    return InvalidOutput((str(e) if isinstance(e, HeatmapError) else "%s: %s" % (type(e).__name__, e)) + suffix)


def validated(model, fs, folder, name):
    """`name` in `folder`, validated as `model`; InvalidOutput says which file and what is wrong."""
    try:
        return model.model_validate(read_json(fs, fs.join(folder, name)))
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, name)


def write_run_output(fs, out, report_path, result, secrets):
    """report.md (`report_path` redacted, or a note that there is none) and, when there is a result, result.json into
    `out`. The result counts the report's redactions too."""
    report, n = sanitize_report(fs.read_text(report_path) if fs.exists(report_path) else "_no report written_", secrets)
    fs.write_text(fs.join(out, "report.md"), report)
    if result is not None:
        result.redactions += n
        write_json(fs, fs.join(out, "result.json"), result)
