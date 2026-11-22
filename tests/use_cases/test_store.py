"""The files of a run (use_cases/store.py), through a mock of the FileSystem interface: JSON, the heat map, a reviewer's
judgement, the policy and GitHub step outputs."""

import json

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.domain.errors import (
    HeatmapError,
    InvalidPolicy,
)
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


class TestJson:
    """JSON files: models inside plain data are written as JSON, and reading what isn't JSON is an error."""

    def test_models_inside_are_written_as_json(
        self,
        fs,
        heatmap,
    ):
        write_json(fs, "/out/x.json", {"heatmap": heatmap})
        data = read_json(fs, "/out/x.json")
        assert isinstance(data, dict)
        assert data["heatmap"]["repo"] == "demo"

    @pytest.mark.parametrize("files", [{"/out/bad.json": lf("not_json")}], indirect=True)
    def test_a_file_that_is_not_json_is_an_error(
        self,
        fs,
    ):
        with pytest.raises(HeatmapError):
            read_json(fs, "/out/bad.json")


class TestHeatmapFile:
    """The rated heat map's file: saved and loaded back whole, and refused when it is missing or isn't a heat map."""

    @pytest.mark.parametrize("ratings", [lf("two_rated_modules")], indirect=True)
    def test_the_heatmap_round_trips_with_its_facts(
        self,
        fs,
        heatmap,
    ):
        save_heatmap(fs, heatmap, "/out/data.json")
        assert load_heatmap(fs, "/out/data.json") == heatmap

    def test_a_missing_file_is_an_error(
        self,
        fs,
    ):
        with pytest.raises(HeatmapError):
            load_heatmap(fs, "/out/missing.json")

    @pytest.mark.parametrize("files", [{"/out/data.json": lf("not_a_heatmap")}], indirect=True)
    def test_a_file_that_is_not_a_heat_map_is_an_error(
        self,
        fs,
    ):
        with pytest.raises(HeatmapError, match="is not a heat map"):
            load_heatmap(fs, "/out/data.json")


class TestLoadJudgement:
    """A reviewer's judgement file."""

    @pytest.mark.parametrize("files", [{"/j.json": lf("partly_valid_judgement")}], indirect=True)
    def test_judgements_keep_only_valid_levels(
        self,
        fs,
    ):
        loaded = load_judgement(fs, "/j.json")
        assert isinstance(loaded, Judgement)
        assert list(loaded.modules["m"]) == ["business"]


class TestLoadPolicy:
    """The policy of a run: a file given, else the repository's .clean-eval/risk-model.json unless it is a symlink, else
    the defaults."""

    def test_without_a_policy_file_the_defaults(
        self,
        fs,
    ):
        assert load_policy(fs, "/repo") == (Policy(), "clean-eval default")

    @pytest.mark.parametrize("files", [{lf("checkout_policy_file"): lf("high_unrated_policy")}], indirect=True)
    def test_the_repositorys_policy_file_overrides_the_defaults(
        self,
        fs,
    ):
        loaded, source = load_policy(fs, "/repo")
        assert (loaded.unrated_band, source) == ("High", ".clean-eval/risk-model.json")

    @pytest.mark.parametrize(
        "files",
        [{lf("checkout_policy_file"): lf("high_unrated_policy"), "/bad.json": lf("not_json")}],
        indirect=True,
    )
    def test_a_policy_file_given_is_read_and_must_be_valid(
        self,
        fs,
    ):
        with pytest.raises(InvalidPolicy):
            load_policy(fs, "/repo", "/bad.json")

    @pytest.mark.parametrize(
        ("files", "links"),
        [({"/elsewhere.json": json.dumps({"unrated_band": "Low"})}, {lf("checkout_policy_file"): "/elsewhere.json"})],
        indirect=True,
    )
    def test_a_symlinked_policy_is_not_read(
        self,
        fs,
    ):
        assert load_policy(fs, "/repo") == (Policy(), "clean-eval default")
        fs.read_text.assert_not_called()


class TestWriteStepOutputs:
    """GitHub Actions step outputs."""

    @pytest.mark.parametrize("files", [{"/gh_out": "a=1\n"}], indirect=True)
    def test_step_outputs_are_appended_one_per_line(
        self,
        fs,
        memory_fs,
    ):
        write_step_outputs(
            fs,
            "/gh_out",
            StepOutputs(
                {"verdict": "approve", "blockers": "0"},
            ),
        )
        assert memory_fs.read_text("/gh_out") == "a=1\nverdict=approve\nblockers=0\n"
        fs.append_text.assert_called_once_with("/gh_out", "verdict=approve\nblockers=0\n")
