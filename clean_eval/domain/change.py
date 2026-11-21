"""The risk a change carries: each changed path takes the band of the module it belongs to, as rated at the change's base."""
from .classify import ignored
from .entities import ChangeRisk, ModuleRisk
from .modules import module_of
from .scoring import band, band_floor, severity


def _unrated_key(path, names):
    """Name for a path outside every rated module: the first folder level that leaves the rated tree (or the file)."""
    parts = path.split("/")
    for i in range(1, len(parts)):
        prefix = "/".join(parts[:i])
        if not any(n == prefix or n.startswith(prefix + "/") for n in names):
            return prefix
    return path


def assess_change(heatmap, paths, policy):
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
            touched.setdefault(name, ModuleRisk(module=name, band=band(m.scores["overall"]), score=m.scores["overall"],
                                                scores=dict(m.scores), evidence=m.evidence, rated=True, facts=m.facts))
        else:
            key = _unrated_key(path, by_name)
            touched.setdefault(key, ModuleRisk(module=key, band=policy.unrated_band, score=None, scores={}, evidence={}, rated=False))
    risks = sorted(touched.values(), key=lambda r: (-severity(r.band), -(r.score or 0), r.module))
    scores = [r.score for r in risks if r.rated] + [band_floor(r.band) for r in risks if not r.rated]
    return ChangeRisk(files=files, modules=risks, ignored=skipped,
                      max_band=risks[0].band if risks else "Low", max_score=max(scores, default=0))
