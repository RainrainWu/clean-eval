"""The files of a run, through the FileSystem interface: JSON, the policy overrides, the rated heat map, a reviewer's
judgement, and GitHub step outputs."""

import json
from collections.abc import Sequence
from typing import Optional, Union

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.domain.errors import HeatmapError, InvalidOutput
from clean_eval.domain.evaluation import EvalResult
from clean_eval.domain.policy import REPO_POLICY, Policy
from clean_eval.domain.rating import Heatmap, Judgement
from clean_eval.domain.review import Review, StepOutputs
from clean_eval.use_cases.interfaces import FileSystem
from clean_eval.use_cases.rules.review import sanitize_report


def read_json(
    fs: FileSystem,
    path: str,
) -> object:
    """The parsed JSON file (to validate before use); HeatmapError when it can't be read or parsed."""
    try:
        return json.loads(fs.read_text(path))
    except ValueError as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def write_json(
    fs: FileSystem,
    path: str,
    obj: object,
) -> None:
    """Write models (or plain data holding them) as JSON."""
    fs.write_text(path, json.dumps(to_jsonable_python(obj), indent=2))


def load_policy(
    fs: FileSystem,
    repo_root: Optional[str] = None,
    path: Optional[str] = None,
) -> tuple[Policy, str]:
    """(Policy, source): `path`, else <repo>/.clean-eval/risk-model.json when present, else the defaults."""
    if path:
        return Policy.from_text(fs.read_text(path), path), path
    local = fs.join(repo_root or ".", REPO_POLICY)
    if fs.is_file(local) and not fs.is_link(local):
        return Policy.from_text(fs.read_text(local), REPO_POLICY), REPO_POLICY
    return Policy(), "clean-eval default"


def save_heatmap(
    fs: FileSystem,
    heatmap: Heatmap,
    path: str,
) -> None:
    fs.write_text(path, heatmap.model_dump_json(indent=1))


def load_heatmap(
    fs: FileSystem,
    path: str,
) -> Heatmap:
    try:
        return Heatmap.model_validate_json(fs.read_text(path))
    except ValidationError as e:
        raise HeatmapError.from_validation(e, "%s is not a heat map" % path)


def load_judgement(
    fs: FileSystem,
    path: str,
) -> Judgement:
    """The levels in a judgement file; entries that don't validate are dropped (see Judgement)."""
    return Judgement.model_validate(read_json(fs, path))


def write_step_outputs(
    fs: FileSystem,
    path: str,
    outputs: StepOutputs,
) -> None:
    """Append the StepOutputs (validated: one line each) as `name=value` lines to a GitHub Actions step-outputs file."""
    fs.append_text(path, "".join("%s=%s\n" % kv for kv in outputs.root.items()))


# What judging a malformed agent output (or edit) can raise: it then counts as invalid and is restored, never a crash.
JUDGING_ERRORS = (HeatmapError, ArithmeticError, ValueError, KeyError, TypeError, AttributeError)


def invalid(
    error: Exception,
    suffix: str = "",
) -> InvalidOutput:
    """The InvalidOutput that a failure to judge the CI agent's output becomes."""
    if isinstance(error, ValidationError):
        return InvalidOutput(str(InvalidOutput.from_validation(error)) + suffix)
    return InvalidOutput(
        (str(error) if isinstance(error, HeatmapError) else "%s: %s" % (type(error).__name__, error)) + suffix
    )


def validated[M: BaseModel](
    model: type[M],
    fs: FileSystem,
    folder: str,
    name: str,
) -> M:
    """`name` in `folder`, validated as `model`; InvalidOutput says which file and what is wrong."""
    try:
        return model.model_validate(read_json(fs, fs.join(folder, name)))
    except ValidationError as e:
        raise InvalidOutput.from_validation(e, name)


def write_run_output(
    fs: FileSystem,
    out: str,
    report_path: str,
    result: Optional[Union[Review, EvalResult]],
    secrets: Sequence[str],
) -> None:
    """report.md (`report_path` redacted, or a note that there is none) and, when there is a result, result.json into
    `out`. The result counts the report's redactions too."""
    report, n = sanitize_report(fs.read_text(report_path) if fs.exists(report_path) else "_no report written_", secrets)
    fs.write_text(fs.join(out, "report.md"), report)
    if result is not None:
        result.redactions += n
        write_json(fs, fs.join(out, "result.json"), result)
