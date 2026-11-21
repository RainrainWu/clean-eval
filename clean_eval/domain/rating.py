"""Rate one module: run the four aspect rules over its facts."""
from . import aspects
from .entities import ModuleRating, facts_from_dict, facts_to_dict
from .scoring import overall


def score_facts(f, p):
    """(scores, evidence, test info) of one module's Facts under policy `p`."""
    biz, biz_ev = aspects.business(f, p)
    eff, eff_ev = aspects.effects(f, p)
    ent, ent_ev = aspects.entropy(f, p)
    unv, unv_ev, info = aspects.unverified(f, eff, p)
    scores = {"biz": round(biz), "effects": round(eff), "entropy": round(ent), "unverified": round(unv) if unv is not None else None}
    scores["overall"] = overall(scores, p.overall_peak_share)
    return scores, {"biz": biz_ev, "effects": eff_ev, "entropy": ent_ev, "unverified": unv_ev}, info


def rate_module(f, top_files, p):
    scores, evidence, info = score_facts(f, p)
    baseline = {k: v for k, v in scores.items() if k != "overall"}
    return ModuleRating(name=f.name, loc=f.loc, files=f.files, scores=scores, baseline=baseline, tests=info,
                        evidence=evidence, top_files=top_files, facts=facts_to_dict(f))


def rescore(facts, p):
    """Static scores of a module from its stored facts (a dict) under another policy: the basis of calibration replays."""
    return score_facts(facts_from_dict(facts), p)[0]
