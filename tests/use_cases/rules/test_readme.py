"""The README convention (use_cases/rules/readme.py): the heat-map block between its markers, and its fingerprint."""
import unittest

from tests.domain.builders import heatmap, rating

from clean_eval.use_cases.rules.readme import MARKERS, SECTION_HEADING, block_fingerprint, fingerprint, merge_readme


class ReadmeConvention(unittest.TestCase):
    BLOCK = MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"

    def test_block_is_appended_with_a_heading_or_replaced_between_markers(self):
        text, action = merge_readme("# Title\n\nIntro.\n", self.BLOCK)
        self.assertEqual(action, "ADD")
        self.assertTrue(text.startswith("# Title\n\nIntro.\n\n" + SECTION_HEADING + "\n\n"))
        old = "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"
        text, action = merge_readme(old, self.BLOCK)
        self.assertEqual(action, "UPDATE")
        self.assertTrue("new" in text and "old table" not in text and text.endswith("\n\n## Next\nkeep\n"))
        self.assertEqual(merge_readme(None, self.BLOCK)[0], SECTION_HEADING + "\n\n" + self.BLOCK)

    def test_heading_without_markers_gets_its_body_replaced(self):
        text, action = merge_readme("# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n", self.BLOCK)
        self.assertEqual(action, "UPDATE")
        self.assertNotIn("stale", text)
        self.assertEqual(text.count(SECTION_HEADING), 1)
        self.assertTrue(text.endswith("## Next\nkeep\n"))

    def test_fingerprint_ignores_commit_and_date_but_not_scores(self):
        a, b = heatmap(rating()), heatmap(rating())
        b.sha, b.generated_at = "fff0000", "2030-01-01"
        self.assertEqual(fingerprint(a), fingerprint(b))
        self.assertNotEqual(fingerprint(a), fingerprint(heatmap(rating(biz=41))))
        self.assertEqual(block_fingerprint("x\n" + self.BLOCK), "0123456789ab")
        self.assertIsNone(block_fingerprint("no block"))


if __name__ == "__main__":
    unittest.main()
