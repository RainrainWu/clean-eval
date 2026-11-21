"""The files of one run: the policy overrides, the rated heatmap, the reviewer's judgement, the README with its diagram,
and the folders of a CI run (precomputed context, the agent's output, the final output) with its step outputs."""
import json
import os
from pathlib import Path

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from ...domain.errors import HeatmapError
from ...domain.judgement import Judgement
from ...domain.policy import REPO_POLICY, Policy
from ...domain.rating import Heatmap


def read_text(path):
    """The file's text (undecodable bytes replaced); HeatmapError when it can't be read."""
    try:
        return Path(path).read_text(errors="replace")
    except OSError as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError) as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def load_policy(repo_root=None, path=None):
    """(Policy, source): `path`, else <repo>/.clean-eval/risk-model.json when present, else the defaults."""
    if path:
        return Policy.from_text(read_text(path), path), path
    local = os.path.join(repo_root or ".", REPO_POLICY)
    if os.path.isfile(local) and not os.path.islink(local):
        return Policy.from_text(read_text(local), REPO_POLICY), REPO_POLICY
    return Policy(), "clean-eval default"


class Workspace:
    def save_heatmap(self, heatmap, path):
        write_text(path, heatmap.model_dump_json(indent=1))

    def load_heatmap(self, path):
        try:
            return Heatmap.model_validate_json(read_text(path))
        except ValidationError as e:
            raise HeatmapError.from_validation(e, "%s is not a heat map" % path)

    def load_judgement(self, path):
        """The levels in a judgement file; entries that don't validate are dropped (see Judgement)."""
        return Judgement.model_validate(read_json(path))


class FileReadmeStore:
    """ReadmeStore over the README and the diagram file of a working tree."""

    def __init__(self, readme_path, svg_path):
        self.readme_path, self.svg_path = readme_path, svg_path

    def read_readme(self):
        try:
            return Path(self.readme_path).read_text()
        except FileNotFoundError:
            return None

    def svg_exists(self):
        return os.path.isfile(self.svg_path)

    def image_link(self):
        return os.path.relpath(self.svg_path, os.path.dirname(os.path.abspath(self.readme_path))).replace(os.sep, "/")

    def write(self, readme_text, svg_text):
        write_text(self.svg_path, svg_text)
        write_text(self.readme_path, readme_text if readme_text.endswith("\n") else readme_text + "\n")


class Folder:
    """One folder of a CI run; names are relative to it."""

    def __init__(self, root):
        self.root = root

    def path(self, name):
        return os.path.join(self.root, name)

    def exists(self, name):
        return os.path.exists(self.path(name))

    def read_text(self, name):
        return read_text(self.path(name))

    def read_json(self, name):
        return read_json(self.path(name))

    def write_text(self, name, text):
        write_text(self.path(name), text)

    def write_json(self, name, obj):
        """Write models (or plain data holding them) as JSON."""
        self.write_text(name, json.dumps(to_jsonable_python(obj), indent=2))


def append_step_outputs(path, outputs):
    """Append the StepOutputs (validated: one line each) as `name=value` lines to a GitHub Actions step-outputs file."""
    with open(path, "a") as f:
        f.writelines("%s=%s\n" % kv for kv in outputs.root.items())
