# Publish

The heat map has one output: the README's `## Risk Heat Map` section and the SVG it shows. It comes from
`<OUT>/data.json` (plus `judgement.json`; drop `--judgement` when there is none). Tile area is module lines, tests
excluded; tile colour is the band from [rate.md](rate.md), repeated as a glyph and number so colour is never the only
signal; N/A is grey. `CE` is the command from SKILL.md.

## README section

`CE readme <OUT>/data.json [--judgement <OUT>/judgement.json] [--readme README.md] [--svg docs/risk-heatmap.svg]`:

- writes the SVG treemap (dark backing, so it reads in GitHub's light and dark themes) and puts, between
  `<!-- risk-heat-map:start -->` and `<!-- risk-heat-map:end -->` under `## Risk Heat Map`, the image, a caption (ref,
  commit, date, "heuristics, not a verdict") and the band legend;
- creates the section at the end of the README when it is missing, and replaces only what is between the markers;
- keeps a fingerprint of the ratings inside the block: when a new run rates every module the same, nothing is written,
  so a scheduled refresh opens no pull request (the README then still shows the commit of the last real change).

`CE report <OUT>/data.json [--judgement ...]` prints a Markdown summary of the riskiest modules for the chat; it does
not publish anything.

## In CI

The GitHub Action (`skill: risk-heat-map`) rates the checkout, refreshes README and SVG in the workspace (output
`readme-changed`) and puts the summary in the job summary. It never commits: the example workflow opens a pull request
with `README.md` and `docs/risk-heatmap.svg` when they changed.
