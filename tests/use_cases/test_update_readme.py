"""Refreshing the README's heat-map section and its diagram (use_cases/update_readme.py), through the FileSystem
interface and a DiagramPresenter."""
import unittest

from tests.domain.builders import heatmap, rating
from tests.use_cases.fakes import MemoryFileSystem, fake

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.interfaces import DiagramPresenter
from clean_eval.use_cases.rules.readme import MARKERS
from clean_eval.use_cases.update_readme import update_readme

P = Policy()
README, SVG = "/repo/README.md", "/repo/docs/risk-heatmap.svg"
DIAGRAM = fake(DiagramPresenter, svg=lambda heatmap: "<svg/>", readme_block=lambda heatmap, link, fp: "%s\n![map](%s)\n"
               "<!-- risk-heat-map:fingerprint=%s -->\n%s\n" % (MARKERS[0], link, fp, MARKERS[1]))


class UpdateReadmeTest(unittest.TestCase):
    def setUp(self):
        self.fs = MemoryFileSystem({README: "# Demo\n"})

    def update(self, h, readme=README):
        return update_readme(self.fs, DIAGRAM, readme, SVG, h, None, P)

    def test_writes_when_ratings_change_and_not_otherwise(self):
        first = self.update(heatmap(rating()))
        self.assertEqual((first.changed, first.action), (True, "ADD"))
        self.assertIn("![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint,
                      self.fs.read_text(README))
        self.assertEqual(self.fs.read_text(SVG), "<svg/>")
        self.assertFalse(self.update(heatmap(rating())).changed)  # the same ratings: nothing to write
        self.fs.remove(SVG)                                        # diagram deleted: rewritten though the ratings match
        self.assertTrue(self.update(heatmap(rating())).changed)
        moved = self.update(heatmap(rating(biz=90)))
        self.assertEqual((moved.changed, moved.action), (True, "UPDATE"))

    def test_the_image_is_linked_relative_to_the_readme(self):
        self.update(heatmap(rating()), "/repo/docs/README.md")
        text = self.fs.read_text("/repo/docs/README.md")
        self.assertIn("![map](risk-heatmap.svg)", text)
        self.assertTrue(text.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
