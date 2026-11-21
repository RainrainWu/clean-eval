"""How bands look: the status palette, emoji and glyphs shared by the SVG and the Markdown summary."""

# band -> (colour, emoji, glyph). Tiles are drawn translucent: a tint of the colour behind theme-coloured text, with a solid border.
BAND_STYLE = {
    "Low": ("#0ca30c", "🟢", "✓"),
    "Moderate": ("#fab219", "🟡", "!"),
    "High": ("#ec835a", "🟠", "▲"),
    "Critical": ("#d03b3b", "🔴", "✕"),
    "N/A": ("#898781", "⚪", "–"),
}
TILE_OPACITY = 0.35
DIAGRAM_BACKING, DIAGRAM_TEXT = "#0d1117", "#e6edf3"  # the SVG brings its own dark backing: GitHub's page theme can differ from the OS theme
MIN_TILE_SHARE = 0.015  # tiles under this share of the code are drawn larger so they stay readable
DIAGRAM_PATH = "docs/risk-heatmap.svg"  # default location of the README diagram, relative to the repository root
