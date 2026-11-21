# Publish

Both outputs come from `<OUT>/data.json` (plus `judgement.json`; drop `--judgement` when there is none). Tile area is
module lines, tests excluded; tile colour is the band from [rate.md](rate.md), repeated as a glyph and word so colour is
never the only signal; N/A is grey. `CE` is the command from SKILL.md.

## README section (default)

`CE readme <OUT>/data.json [--judgement <OUT>/judgement.json] [--readme README.md] [--svg docs/risk-heatmap.svg]`:

- writes the SVG treemap (dark backing, so it reads in GitHub's light and dark themes) and puts, between
  `<!-- risk-heat-map:start -->` and `<!-- risk-heat-map:end -->` under `## Risk Heat Map`, the image, a caption (ref,
  commit, date, "heuristics, not a verdict") and the band legend;
- creates the section at the end of the README when it is missing, and replaces only what is between the markers;
- keeps a fingerprint of the ratings inside the block: when a new run rates every module the same, nothing is written,
  so a scheduled refresh opens no pull request (the README then still shows the commit of the last real change).

`CE diagram <OUT>/data.json [--judgement ...] --out-dir <dir>` writes the same SVG and block as files, without touching
the README.

## Interactive page (on request)

1. `CE render <OUT>/data.json [--judgement <OUT>/judgement.json] [--suffix " Detail"] --out <OUT>/heatmap.html`.
2. The page has a tab for Overall and each aspect (URL hash), a detail panel on tile or table-row click (overall, aspect
   bars, evidence, test links, largest files), a hover tooltip, a sortable table view, and the top module preselected.
3. Load `artifact-design` and `dataviz` if not loaded (the palette is fixed, no re-validation). Publish with `Artifact`:
   `file_path: <OUT>/heatmap.html`, `icon: heatmap`, a one-sentence `description`; title `<repo> Risk Heat Map` plus the
   suffix for a variant with another `--depth`. It is private until the user shares it.

## In CI

The GitHub Action (`skill: risk-heat-map`) rates the checkout, updates README and SVG in the workspace (output
`readme-changed`), writes the page (output `page`) and a Markdown summary to the job summary. It never commits: the
example workflow opens a pull request with `README.md` and `docs/risk-heatmap.svg` when they changed.
