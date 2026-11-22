"""Use case: put the heat map into the repository's README (the `## Risk Heat Map` section) and its diagram file,
through the FileSystem interface and a DiagramPresenter.

Nothing is written when the ratings are the same as in the README already (same fingerprint, diagram present): a
scheduled refresh then has nothing to propose. The README keeps the commit and date of the last real change."""
from clean_eval.domain.rating import ReadmeUpdate
from clean_eval.use_cases.finalize_heatmap import finalize_heatmap
from clean_eval.use_cases.rules.readme import block_fingerprint, fingerprint, merge_readme


def update_readme(fs, presenter, readme_path, svg_path, heatmap, judgement, policy):
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    fp = fingerprint(heatmap)
    existing = fs.read_text(readme_path) if fs.is_file(readme_path) else None
    if block_fingerprint(existing) == fp and fs.is_file(svg_path):
        return ReadmeUpdate(heatmap=heatmap, changed=False, fingerprint=fp)
    link = fs.relative(svg_path, fs.parent(fs.absolute(readme_path))).replace(fs.sep, "/")
    text, action = merge_readme(existing, presenter.readme_block(heatmap, link, fp))
    fs.write_text(svg_path, presenter.svg(heatmap))
    fs.write_text(readme_path, text if text.endswith("\n") else text + "\n")
    return ReadmeUpdate(heatmap=heatmap, changed=True, action=action, fingerprint=fp)
