"""The risk a change carries: each changed path takes the band of the module it belongs to, as rated at the change's base."""

from clean_eval.domain.change import ChangeRisk, ModuleRisk
from clean_eval.use_cases.rules.classify import ignored
from clean_eval.use_cases.rules.modules import module_of
from clean_eval.use_cases.rules.scoring import band, band_floor, severity


def _unrated_key(path, names):
    """Name for a path outside every rated module: the first folder level that leaves the rated tree (or the file)."""
    parts = path.split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        if not any(n == prefix or n.startswith(prefix + "/") for n in names):
            return prefix
    return path


def change_risk(heatmap, paths, policy):
    """The ChangeRisk of changing `paths`, from the heat map of the change's base."""
    by_name = {m.name: m for m in heatmap.modules}
    files, touched, skipped = {}, {}, []
    for path in paths:
        if ignored(path, policy):
            files[path] = None
            skipped.append(path)
            continue
        name = module_of(path, by_name)
        files[path] = name
        if name:
            m = by_name[name]
            touched.setdefault(
                name,
                ModuleRisk(
                    module=name,
                    band=band(m.scores.overall),
                    score=m.scores.overall,
                    scores=m.scores.model_copy(),
                    evidence=m.evidence,
                    rated=True,
                    facts=m.facts,
                ),
            )
        else:
            key = _unrated_key(path, by_name)
            touched.setdefault(key, ModuleRisk(module=key, band=policy.unrated_band, rated=False))
    risks = sorted(touched.values(), key=lambda r: (-severity(r.band), -(r.score or 0), r.module))
    scores = [r.score for r in risks if r.rated] + [band_floor(r.band) for r in risks if not r.rated]
    return ChangeRisk(
        files=files,
        modules=risks,
        ignored=skipped,
        max_band=risks[0].band if risks else "Low",
        max_score=max(scores, default=0),
    )
