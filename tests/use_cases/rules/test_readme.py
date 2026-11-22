"""The README convention (use_cases/rules/readme.py): the heat-map block between its markers, and its fingerprint."""

from clean_eval.use_cases.rules.readme import MARKERS, SECTION_HEADING, block_fingerprint, fingerprint, merge_readme
from tests.domain.builders import heatmap, rating

BLOCK = MARKERS[0] + "\n<!-- risk-heat-map:fingerprint=0123456789ab -->\nnew\n" + MARKERS[1] + "\n"


def test_block_is_appended_with_a_heading_or_replaced_between_markers():
    text, action = merge_readme("# Title\n\nIntro.\n", BLOCK)
    assert action == "ADD"
    assert text.startswith("# Title\n\nIntro.\n\n" + SECTION_HEADING + "\n\n")
    old = "# T\n" + SECTION_HEADING + "\n\n" + MARKERS[0] + "\nold table\n" + MARKERS[1] + "\n\n## Next\nkeep\n"
    text, action = merge_readme(old, BLOCK)
    assert action == "UPDATE"
    assert "new" in text
    assert "old table" not in text
    assert text.endswith("\n\n## Next\nkeep\n")
    assert merge_readme(None, BLOCK)[0] == SECTION_HEADING + "\n\n" + BLOCK


def test_heading_without_markers_gets_its_body_replaced():
    text, action = merge_readme("# T\n\n## Risk Heat Map\nstale table\n\n## Next\nkeep\n", BLOCK)
    assert action == "UPDATE"
    assert "stale" not in text
    assert text.count(SECTION_HEADING) == 1
    assert text.endswith("## Next\nkeep\n")


def test_fingerprint_ignores_commit_and_date_but_not_scores():
    a, b = heatmap(rating()), heatmap(rating())
    b.sha, b.generated_at = "fff0000", "2030-01-01"
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(heatmap(rating(biz=41)))
    assert block_fingerprint("x\n" + BLOCK) == "0123456789ab"
    assert block_fingerprint("no block") is None
