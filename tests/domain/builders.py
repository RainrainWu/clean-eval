"""Builders of domain models for any layer's tests, from the domain alone."""

from collections import Counter

from clean_eval.domain.rating import (
    AspectScores,
    Evidence,
    Facts,
    Heatmap,
    ModuleRating,
    ModuleTests,
    Scores,
    SourceFile,
)


def facts(**kw):
    base = dict(
        name="src/x",
        loc=1000,
        files=5,
        share=0.1,
        funcs=[(10, 2)] * 20,
        typed_share=1.0,
        broad=0,
        swallow=0,
        todo=0,
        big_files=0,
        fan_in=0,
        fan_out=0,
        effect_counts=Counter(),
        effect_examples={},
        migrations=0,
        entry_flows=[],
        flow_touch=0.0,
        keyword=False,
        tooling=False,
        graph_functions=20,
        linked_functions=20,
        linked_test_files=3,
        linked_test_loc=1000.0,
        test_scales=Counter(unit=2, integration=1),
        line_rate=None,
    )
    base.update(kw)
    return Facts(**base)


def sf(path, loc, text="", test=False, blob=""):
    return SourceFile(path=path, text=text, loc=loc, is_test=test, blob=blob)


def rating(name="m", loc=100, biz=40, effects=10, entropy=20, unverified=30, fact=None, overall=None):
    """A ModuleRating. Its overall score is the rules' to compute (use_cases/rules/heatmap.py); here it is `overall`, by
    default the worst aspect."""
    aspect = AspectScores(biz=biz, effects=effects, entropy=entropy, unverified=unverified)
    worst = max(v for v in (biz, effects, entropy, unverified) if v is not None)
    scores = Scores(**aspect.model_dump(), overall=worst if overall is None else overall)
    return ModuleRating(
        name=name,
        loc=loc,
        files=1,
        scores=scores,
        baseline=aspect,
        evidence=Evidence(biz=["e"], effects=["e"], entropy=["e"], unverified=["e"]),
        tests=ModuleTests(functions=0, tested=0, coverage=None, files=0, loc=0, ratio=0),
        facts=fact,
    )


def heatmap(*modules):
    mods = list(modules) or [rating()]
    return Heatmap(
        repo="demo",
        ref="main",
        sha="abc1234",
        generated_at="2026-11-16",
        loc_total=sum(m.loc for m in mods),
        modules=mods,
    )
