"""The files of a run (use_cases/store.py), through the FileSystem interface: JSON, the policy, the heat map, a reviewer's
judgement and GitHub step outputs."""
import json
import unittest

from tests.domain.builders import facts, heatmap, rating
from tests.use_cases.fakes import MemoryFileSystem

from clean_eval.domain.ci import StepOutputs
from clean_eval.domain.errors import HeatmapError, InvalidPolicy
from clean_eval.domain.judgement import Judgement
from clean_eval.domain.policy import Policy
from clean_eval.use_cases.store import (load_heatmap, load_judgement, load_policy, read_json, save_heatmap, write_json,
                                        write_step_outputs)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.fs = MemoryFileSystem()

    def test_the_heatmap_round_trips_with_its_facts(self):
        h = heatmap(rating("a", 10, fact=facts(name="a")), rating("b", 20, unverified=None))
        save_heatmap(self.fs, h, "/out/data.json")
        self.assertEqual(load_heatmap(self.fs, "/out/data.json"), h)
        with self.assertRaises(HeatmapError):
            load_heatmap(self.fs, "/out/missing.json")
        self.fs.write_text("/out/data.json", json.dumps({"repo": "x", "modules": [{"name": "m"}]}))
        with self.assertRaises(HeatmapError) as cm:
            load_heatmap(self.fs, "/out/data.json")
        self.assertIn("is not a heat map", str(cm.exception))

    def test_json_with_models_inside(self):
        write_json(self.fs, "/out/x.json", {"heatmap": heatmap()})
        self.assertEqual(read_json(self.fs, "/out/x.json")["heatmap"]["repo"], "demo")
        self.fs.write_text("/out/bad.json", "{nope")
        with self.assertRaises(HeatmapError):
            read_json(self.fs, "/out/bad.json")

    def test_judgements_keep_only_valid_levels(self):
        self.fs.write_text("/j.json", json.dumps({"modules": {"m": {"business": {"level": 1, "reason": "core"},
                                                                    "entropy": {"level": 7}}}}))
        judgement = load_judgement(self.fs, "/j.json")
        self.assertIsInstance(judgement, Judgement)
        self.assertEqual(list(judgement.modules["m"]), ["business"])

    def test_policy_sources(self):
        self.assertEqual(load_policy(self.fs, "/repo"), (Policy(), "clean-eval default"))
        self.fs.write_text("/repo/.clean-eval/risk-model.json", json.dumps({"unrated_band": "High"}))
        policy, source = load_policy(self.fs, "/repo")
        self.assertEqual((policy.unrated_band, source), ("High", ".clean-eval/risk-model.json"))
        self.fs.write_text("/bad.json", "{nope")
        with self.assertRaises(InvalidPolicy):
            load_policy(self.fs, "/repo", "/bad.json")

    def test_a_symlinked_policy_is_not_read(self):
        self.fs.write_text("/elsewhere.json", json.dumps({"unrated_band": "Low"}))
        self.fs.link("/repo/.clean-eval/risk-model.json", "/elsewhere.json")
        self.assertEqual(load_policy(self.fs, "/repo"), (Policy(), "clean-eval default"))

    def test_step_outputs_are_appended_one_per_line(self):
        self.fs.write_text("/gh_out", "a=1\n")
        write_step_outputs(self.fs, "/gh_out", StepOutputs({"verdict": "approve", "blockers": "0"}))
        self.assertEqual(self.fs.read_text("/gh_out"), "a=1\nverdict=approve\nblockers=0\n")


if __name__ == "__main__":
    unittest.main()
