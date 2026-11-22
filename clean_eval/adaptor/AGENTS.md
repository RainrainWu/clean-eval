# clean_eval/adaptor

This layer holds only the presenters. They implement `DiagramPresenter` and `ReportPresenter` from
`clean_eval/use_cases/interfaces.py`.
- `presenters/diagram.py` (`SvgDiagramPresenter`) renders the README's SVG treemap and the block between the heat-map
  markers.
- `presenters/markdown_report.py` (`MarkdownReportPresenter`) renders the Markdown summary.
- `presenters/style.py` holds the colors, bands and the diagram's path; `presenters/treemap.py` holds the squarified
  layout.

Rules:
- **Rendering only, no I/O.** A presenter returns text, and the use case writes it.
- **Escape every name you render.** Module names are repository paths and can contain `<`, `&` or `|`.
- **Keep the README block's markers.** It must keep the start and end markers and the fingerprint comment from
  `use_cases/rules/readme.py`. `update_readme` reads the fingerprint back to skip a rewrite when nothing changed.
- **Tests** go in `tests/adaptor/presenters/test_<module>.py`.
