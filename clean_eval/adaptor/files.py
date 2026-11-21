"""The files of one run, over the file-system gateway: the policy overrides, the rated heatmap, the reviewer's judgement,
the README with its diagram, and the folders of a CI run (precomputed context, the agent's output, the final output)
with its step outputs. The translation lives here (JSON, models, README paths); the file system does the I/O."""
import json

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from ..domain.errors import HeatmapError
from ..domain.judgement import Judgement
from ..domain.policy import REPO_POLICY, Policy
from ..domain.rating import Heatmap


def read_json(fs, path):
    """The parsed JSON file; HeatmapError when it can't be read or parsed."""
    try:
        return json.loads(fs.read_text(path))
    except ValueError as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def load_policy(fs, repo_root=None, path=None):
    """(Policy, source): `path`, else <repo>/.clean-eval/risk-model.json when present, else the defaults."""
    if path:
        return Policy.from_text(fs.read_text(path), path), path
    local = fs.join(repo_root or ".", REPO_POLICY)
    if fs.is_file(local) and not fs.is_link(local):
        return Policy.from_text(fs.read_text(local), REPO_POLICY), REPO_POLICY
    return Policy(), "clean-eval default"


class Workspace:
    """The heat map and judgement files of a run."""

    def __init__(self, fs):
        self.fs = fs

    def save_heatmap(self, heatmap, path):
        self.fs.write_text(path, heatmap.model_dump_json(indent=1))

    def load_heatmap(self, path):
        try:
            return Heatmap.model_validate_json(self.fs.read_text(path))
        except ValidationError as e:
            raise HeatmapError.from_validation(e, "%s is not a heat map" % path)

    def load_judgement(self, path):
        """The levels in a judgement file; entries that don't validate are dropped (see Judgement)."""
        return Judgement.model_validate(read_json(self.fs, path))


class FileReadmeStore:
    """ReadmeStore over the README and the diagram file of a working tree."""

    def __init__(self, fs, readme_path, svg_path):
        self.fs, self.readme_path, self.svg_path = fs, readme_path, svg_path

    def read_readme(self):
        return self.fs.read_text(self.readme_path) if self.fs.is_file(self.readme_path) else None

    def svg_exists(self):
        return self.fs.is_file(self.svg_path)

    def image_link(self):
        folder = self.fs.parent(self.fs.absolute(self.readme_path))
        return self.fs.relative(self.svg_path, folder).replace(self.fs.sep, "/")

    def write(self, readme_text, svg_text):
        self.fs.write_text(self.svg_path, svg_text)
        self.fs.write_text(self.readme_path, readme_text if readme_text.endswith("\n") else readme_text + "\n")


class Folder:
    """One folder of a CI run; names are relative to it."""

    def __init__(self, fs, root):
        self.fs, self.root = fs, root

    def path(self, name):
        return self.fs.join(self.root, name)

    def exists(self, name):
        return self.fs.exists(self.path(name))

    def read_text(self, name):
        return self.fs.read_text(self.path(name))

    def read_json(self, name):
        return read_json(self.fs, self.path(name))

    def write_text(self, name, text):
        self.fs.write_text(self.path(name), text)

    def write_json(self, name, obj):
        """Write models (or plain data holding them) as JSON."""
        self.write_text(name, json.dumps(to_jsonable_python(obj), indent=2))


def append_step_outputs(fs, path, outputs):
    """Append the StepOutputs (validated: one line each) as `name=value` lines to a GitHub Actions step-outputs file."""
    fs.append_text(path, "".join("%s=%s\n" % kv for kv in outputs.root.items()))
