# clean_eval/adaptor

This layer holds only the presenters, in `presenters.py`. They implement `DiagramPresenter` and `ReportPresenter` from
`clean_eval/use_cases/interfaces.py`:
- `SvgDiagramPresenter` renders the README's SVG treemap and the block between the heat-map markers.
- `MarkdownReportPresenter` renders the Markdown summary.
- The colors and bands they share, and the squarified treemap layout (`squarify`), sit beside them.

Rules:
- **Rendering only, no I/O.** A presenter returns text, and the use case writes it.
- **Escape every name you render.** Module names are repository paths and can contain `<`, `&` or `|`.
- **Keep the README block's markers.** It must keep the start and end markers and the fingerprint comment from
  `use_cases/rules/heatmap.py`. `update_readme` reads the fingerprint back to skip a rewrite when nothing changed.
- **Tests** go in `tests/adaptor/test_presenters.py`.
