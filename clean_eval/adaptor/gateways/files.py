"""The files of one run: the policy overrides, the rated heatmap, the reviewer's judgement, and the README with its diagram."""
import dataclasses
import json
import os
from pathlib import Path

from ...domain.entities import Heatmap, ModuleRating
from ...domain.errors import HeatmapError, InvalidPolicy
from ...domain.policy import Policy

REPO_POLICY = os.path.join(".clean-eval", "risk-model.json")


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError) as e:
        raise HeatmapError("cannot read %s: %s" % (path, e))


def write_text(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def policy_from_text(text, source):
    try:
        overrides = json.loads(text) if text and text.strip() else {}
    except ValueError as e:
        raise InvalidPolicy("%s is not valid JSON: %s" % (source, e))
    try:
        return Policy.from_overrides(overrides)
    except InvalidPolicy as e:
        raise InvalidPolicy("%s: %s" % (source, e))


def load_policy(repo_root=None, path=None):
    """(Policy, source): `path`, else <repo>/.clean-eval/risk-model.json when present, else the defaults."""
    if path:
        return policy_from_text(Path(path).read_text(), path), path
    local = os.path.join(repo_root or ".", REPO_POLICY)
    if os.path.isfile(local) and not os.path.islink(local):
        return policy_from_text(Path(local).read_text(), REPO_POLICY), REPO_POLICY
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
