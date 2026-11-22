"""The files of a run (use_cases/store.py), through the FileSystem interface: JSON, the policy, the heat map, a reviewer's
judgement and GitHub step outputs."""

import json

import pytest

from clean_eval.domain.errors import HeatmapError, InvalidPolicy
from clean_eval.domain.policy import Policy
from clean_eval.domain.rating import Judgement
from clean_eval.domain.review import StepOutputs
from clean_eval.use_cases.store import (
    load_heatmap,
    load_judgement,
    load_policy,
    read_json,
    save_heatmap,
    write_json,
    write_step_outputs,
)
from tests.domain.builders import facts, heatmap, rating


def test_the_heatmap_round_trips_with_its_facts(fs):
    h = heatmap(rating("a", 10, fact=facts(name="a")), rating("b", 20, unverified=None))
    save_heatmap(fs, h, "/out/data.json")
    assert load_heatmap(fs, "/out/data.json") == h
    with pytest.raises(HeatmapError):
        load_heatmap(fs, "/out/missing.json")
    fs.write_text("/out/data.json", json.dumps({"repo": "x", "modules": [{"name": "m"}]}))
    with pytest.raises(HeatmapError) as cm:
        load_heatmap(fs, "/out/data.json")
    assert "is not a heat map" in str(cm.value)


def test_json_with_models_inside(fs):
    write_json(fs, "/out/x.json", {"heatmap": heatmap()})
    assert read_json(fs, "/out/x.json")["heatmap"]["repo"] == "demo"
    fs.write_text("/out/bad.json", "{nope")
    with pytest.raises(HeatmapError):
        read_json(fs, "/out/bad.json")


def test_judgements_keep_only_valid_levels(fs):
    fs.write_text(
        "/j.json",
        json.dumps({"modules": {"m": {"business": {"level": 1, "reason": "core"}, "entropy": {"level": 7}}}}),
    )
    judgement = load_judgement(fs, "/j.json")
    assert isinstance(judgement, Judgement)
    assert list(judgement.modules["m"]) == ["business"]


def test_policy_sources(fs):
    assert load_policy(fs, "/repo") == (Policy(), "clean-eval default")
    fs.write_text("/repo/.clean-eval/risk-model.json", json.dumps({"unrated_band": "High"}))
    policy, source = load_policy(fs, "/repo")
    assert (policy.unrated_band, source) == ("High", ".clean-eval/risk-model.json")
    fs.write_text("/bad.json", "{nope")
    with pytest.raises(InvalidPolicy):
        load_policy(fs, "/repo", "/bad.json")


def test_a_symlinked_policy_is_not_read(fs):
    fs.write_text("/elsewhere.json", json.dumps({"unrated_band": "Low"}))
    fs.link("/repo/.clean-eval/risk-model.json", "/elsewhere.json")
    assert load_policy(fs, "/repo") == (Policy(), "clean-eval default")


def test_step_outputs_are_appended_one_per_line(fs):
    fs.write_text("/gh_out", "a=1\n")
    write_step_outputs(fs, "/gh_out", StepOutputs({"verdict": "approve", "blockers": "0"}))
    assert fs.read_text("/gh_out") == "a=1\nverdict=approve\nblockers=0\n"
