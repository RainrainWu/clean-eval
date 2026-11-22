"""The files of a run, through the FileSystem interface: JSON, the policy overrides, the rated heat map, a reviewer's
judgement, and GitHub step outputs."""

import json

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from clean_eval.domain.errors import HeatmapError
from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import REPO_POLICY, Policy
from clean_eval.domain.rating import Heatmap


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
