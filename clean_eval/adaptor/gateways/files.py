"""The files of one run: the policy overrides, the rated heatmap, the reviewer's judgement, the README with its diagram,
and the folders of a CI run (precomputed context, the agent's output, the final output) with its step outputs."""
import dataclasses
import json
import os
from pathlib import Path

from ...domain.entities import Heatmap, ModuleRating
from ...domain.errors import HeatmapError
from ...domain.policy import REPO_POLICY, Policy, policy_from_text


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
        return policy_from_text(read_text(path), path), path
    local = os.path.join(repo_root or ".", REPO_POLICY)
    if os.path.isfile(local) and not os.path.islink(local):
        return policy_from_text(read_text(local), REPO_POLICY), REPO_POLICY
    return Policy(), "clean-eval default"


class Workspace:
    def save_heatmap(self, heatmap, path):
        write_text(path, json.dumps(dataclasses.asdict(heatmap), indent=1))

    def load_heatmap(self, path):
        d = read_json(path)
        try:
            return Heatmap(**{**d, "modules": [ModuleRating(**m) for m in d["modules"]]})
        except (TypeError, KeyError) as e:
            raise HeatmapError("%s is not a heatmap: %s" % (path, e))

    def load_judgement(self, path):
        return read_json(path)


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
        self.write_text(name, json.dumps(obj, indent=2, default=str))


def append_step_outputs(path, outputs):
    """Append `name=value` lines to a GitHub Actions step-outputs file. The values are enums and numbers clean-eval built;
    one with a line break, which could start another output, is refused."""
    lines = []
    for name, value in outputs.items():
        if "\n" in str(value) or "\r" in str(value):
            raise HeatmapError("step output %s has a line break" % name)
        lines.append("%s=%s\n" % (name, value))
    with open(path, "a") as f:
        f.writelines(lines)
