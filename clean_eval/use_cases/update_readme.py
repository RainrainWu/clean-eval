"""Use case: put the heat map into the repository's README (the `## Risk Heat Map` section) and its diagram file.

Nothing is written when the ratings are the same as in the README already (same fingerprint, diagram present): a
scheduled refresh then has nothing to propose. The README keeps the commit and date of the last real change."""
from typing import Literal, Optional

from pydantic import BaseModel

from ..domain.rating import Heatmap
from .finalize_heatmap import finalize_heatmap
from .rules.readme import block_fingerprint, fingerprint, merge_readme


class ReadmeUpdate(BaseModel):
    heatmap: Heatmap
    changed: bool
    action: Optional[Literal["ADD", "UPDATE"]]  # when changed
    fingerprint: str


class UpdateReadme:
    def __init__(self, presenter, store):
        self.presenter, self.store = presenter, store

    def execute(self, heatmap, judgement, policy):
        heatmap = finalize_heatmap(heatmap, judgement, policy)
        fp = fingerprint(heatmap)
        existing = self.store.read_readme()
        if block_fingerprint(existing) == fp and self.store.svg_exists():
            return ReadmeUpdate(heatmap=heatmap, changed=False, action=None, fingerprint=fp)
        text, action = merge_readme(existing, self.presenter.readme_block(heatmap, self.store.image_link(), fp))
        self.store.write(text, self.presenter.svg(heatmap))
        return ReadmeUpdate(heatmap=heatmap, changed=True, action=action, fingerprint=fp)
