"""The README convention: the heat map lives in a `## Risk Heat Map` section, between two markers, so the next run can
replace it. A fingerprint of the ratings inside the block tells whether a new run changes anything."""

import hashlib
import json
import re

MARKERS = ("<!-- risk-heat-map:start -->", "<!-- risk-heat-map:end -->")
SECTION_HEADING = "## Risk Heat Map"
FINGERPRINT = re.compile(r"<!-- risk-heat-map:fingerprint=([0-9a-f]{12}) -->")


def fingerprint(heatmap):
    """Stable id of what the diagram shows (modules, sizes, scores), independent of the commit and the date."""
    shown = sorted((m.name, m.loc, sorted(m.scores.model_dump().items())) for m in heatmap.modules)
    return hashlib.sha1(json.dumps(shown).encode()).hexdigest()[:12]


def block_fingerprint(readme_text):
    """The fingerprint inside the README's heat-map block, or None."""
    if not readme_text or MARKERS[0] not in readme_text:
        return None
    block = readme_text.split(MARKERS[0], 1)[1].split(MARKERS[1], 1)[0]
    m = FINGERPRINT.search(block)
    return m.group(1) if m else None


def merge_readme(existing, block):
    """Put `block` (markers included) into README text: between existing markers, else under the section heading
    (replacing that section's old body), else as a new section at the end. Returns (new text, ADD or UPDATE)."""
    start, end = MARKERS
    if existing and start in existing and end in existing:
        head, rest = existing.split(start, 1)
        return head + block.rstrip("\n") + rest.split(end, 1)[1], "UPDATE"
    text = existing or ""
    m = re.search(r"^%s[ \t]*$" % re.escape(SECTION_HEADING), text, re.M)
    if m:
        nxt = re.search(r"^## ", text[m.end() :], re.M)
        stop = m.end() + nxt.start() if nxt else len(text)
        tail = text[stop:].lstrip("\n")
        return text[: m.end()] + "\n\n" + block.rstrip("\n") + "\n" + ("\n" + tail if tail else ""), "UPDATE"
    prefix = text.rstrip("\n") + "\n\n" if text.strip() else ""
    return prefix + SECTION_HEADING + "\n\n" + block.rstrip("\n") + "\n", "ADD"
