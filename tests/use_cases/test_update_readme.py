"""Refreshing the README's heat-map section and its diagram (use_cases/update_readme.py), through the FileSystem
interface and a DiagramPresenter."""

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.interfaces import DiagramPresenter
from clean_eval.use_cases.rules.readme import MARKERS
from clean_eval.use_cases.update_readme import update_readme
from tests.domain.builders import heatmap, rating
from tests.use_cases.fakes import fake

P = Policy()
README, SVG = "/repo/README.md", "/repo/docs/risk-heatmap.svg"
DIAGRAM = fake(
    DiagramPresenter,
    svg=lambda heatmap: "<svg/>",
    readme_block=lambda heatmap, link, fp: (
        "%s\n![map](%s)\n<!-- risk-heat-map:fingerprint=%s -->\n%s\n" % (MARKERS[0], link, fp, MARKERS[1])
    ),
)


@pytest.fixture
def fs(fs):
    """The repository's README, before any heat map."""
    fs.write_text(README, "# Demo\n")
    return fs


def update(fs, h, readme=README):
    return update_readme(fs, DIAGRAM, readme, SVG, h, None, P)


def test_writes_when_ratings_change_and_not_otherwise(fs):
    first = update(fs, heatmap(rating()))
    assert (first.changed, first.action) == (True, "ADD")
    assert "![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint in fs.read_text(
        README
    )
    assert fs.read_text(SVG) == "<svg/>"
    assert not update(fs, heatmap(rating())).changed  # the same ratings: nothing to write
    fs.remove(SVG)  # diagram deleted: rewritten though the ratings match
    assert update(fs, heatmap(rating())).changed
    moved = update(fs, heatmap(rating(biz=90)))
    assert (moved.changed, moved.action) == (True, "UPDATE")


def test_the_image_is_linked_relative_to_the_readme(fs):
    update(fs, heatmap(rating()), "/repo/docs/README.md")
    text = fs.read_text("/repo/docs/README.md")
    assert "![map](risk-heatmap.svg)" in text
    assert text.endswith("\n")
