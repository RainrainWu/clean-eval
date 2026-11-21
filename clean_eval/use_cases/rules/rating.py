"""Rate one module: run the four aspect rules over its facts."""
from ...domain.rating import AspectScores, Evidence, ModuleRating, ModuleTests, Scores
from . import aspects
from .scoring import overall


def _score(value):
    """A rule's raw result as a score: rounded, and kept to 0-100 (weights tuned to sum past 1 could overshoot)."""
    return min(100, max(0, round(value)))


def score_facts(f, p):
    """(Scores, Evidence, ModuleTests) of one module's Facts under policy `p`."""
    biz, biz_ev = aspects.business(f, p)
    eff, eff_ev = aspects.effects(f, p)
    ent, ent_ev = aspects.entropy(f, p)
    unv, unv_ev, info = aspects.unverified(f, eff, p)
    aspect = AspectScores(biz=_score(biz), effects=_score(eff), entropy=_score(ent),
                          unverified=_score(unv) if unv is not None else None)
    scores = Scores(**aspect.model_dump(), overall=overall(aspect, p.overall_peak_share))
    return scores, Evidence(biz=biz_ev, effects=eff_ev, entropy=ent_ev, unverified=unv_ev), ModuleTests(**info)


def rate_module(f, top_files, p):
    scores, evidence, tests = score_facts(f, p)
    return ModuleRating(name=f.name, loc=f.loc, files=f.files, scores=scores,
                        baseline=AspectScores(**scores.model_dump(exclude={"overall"})), tests=tests, evidence=evidence,
                        top_files=top_files, facts=f)


def rescore(facts, p):
    """Static scores of a module from its stored Facts under another policy: the basis of calibration replays."""
    return score_facts(facts, p)[0]
