"""Use cases that present a finalized heatmap: the interactive page, the README diagram, and a short summary."""
from dataclasses import dataclass

from ..domain.entities import Heatmap
from ..domain.readme import fingerprint
from .finalize_heatmap import finalize_heatmap

SVG_NAME = "risk-heatmap.svg"
BLOCK_NAME = "readme-block.md"


@dataclass
class RenderedPage:
    heatmap: Heatmap
    html: str


@dataclass
class DiagramFiles:
    heatmap: Heatmap
    svg: str
    readme_block: str
    fingerprint: str


class RenderPage:
    def __init__(self, presenter):
        self.presenter = presenter

    def execute(self, heatmap, judgement, policy, suffix=""):
        heatmap = finalize_heatmap(heatmap, judgement, policy)
        return RenderedPage(heatmap, self.presenter.present(heatmap, suffix))


class BuildDiagram:
    def __init__(self, presenter):
        self.presenter = presenter

    def execute(self, heatmap, judgement, policy, image_link):
        heatmap = finalize_heatmap(heatmap, judgement, policy)
        fp = fingerprint(heatmap)
        return DiagramFiles(heatmap, self.presenter.svg(heatmap), self.presenter.readme_block(heatmap, image_link, fp), fp)


class SummarizeHeatmap:
    def __init__(self, presenter):
        self.presenter = presenter

    def execute(self, heatmap, judgement, policy):
        heatmap = finalize_heatmap(heatmap, judgement, policy)
        return heatmap, self.presenter.report(heatmap)
