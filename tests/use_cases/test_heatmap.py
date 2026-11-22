"""The heat map's use cases (use_cases/heatmap.py), with their dependencies mocked: the sources a rating reads, rating
a snapshot, a working tree (with its coverage) or a revision, the agent's levels, summarizing, and refreshing the
README."""

from unittest.mock import call

import pytest
from pytest_lazy_fixtures import (
    lf,
)

from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.policy import Aspect
from clean_eval.use_cases.heatmap import (
    GENERATED,
    agent_judgement,
    count_loc,
    rate,
    rate_revision,
    rate_working_tree,
    read_revision,
    read_working_tree,
    summarize_heatmap,
    update_readme,
)
from tests.use_cases.fakes import MemoryGit


class TestSourceHelpers:
    """What a read keeps of a file: its lines of code, whether it is generated, and git's id of its content."""

    def test_lines_of_code_leave_out_blank_and_comment_lines(
        self,
    ):
        assert count_loc("a = 1\n\n# note\n  \nb = 2\n") == 2

    @pytest.mark.parametrize(
        "path", ["x_pb2.py", "a/generated/b.py", "vendor/x.py"], ids=["protobuf", "generated folder", "vendored"]
    )
    def test_generated_code_is_recognized(
        self,
        path,
    ):
        assert GENERATED.search("/" + path)


@pytest.mark.parametrize("history", [lf("sources_history")], indirect=True)
class TestSources:
    """A history and a working tree that differs from it: what a rating reads of each."""

    @pytest.mark.parametrize(
        ("git", "files", "links"),
        [({"ls_files.return_value": lf("tracked_paths")}, lf("working_tree_files"), lf("staged_symlink"))],
        indirect=True,
    )
    def test_a_working_tree_is_its_tracked_python_files_as_they_are_on_disk(
        self,
        git,
        fs,
        policy,
        shas,
        edited_source,
    ):
        snapshot, read = read_working_tree(git, fs, policy)
        by_path = {source.path: source for source in read}
        assert sorted(by_path) == ["src/a.py", "tests/test_a.py"]  # not generated, linked, gone, huge or untracked
        assert by_path["src/a.py"].text == edited_source
        assert by_path["src/a.py"].blob == MemoryGit.blob_id(edited_source)  # git's id: caches are shared
        assert by_path["tests/test_a.py"].is_test
        assert (snapshot.repo, snapshot.ref, snapshot.sha, snapshot.root) == ("demo", "main", shas[-1][:7], "/repo")
        assert fs.read_text.call_args_list == [call("/repo/src/a.py"), call("/repo/tests/test_a.py")]  # nothing else

    def test_a_revision_is_read_from_git_objects_sharing_their_contents(
        self,
        git,
        policy,
        shas,
        first_commit_files,
        second_commit_files,
    ):
        blobs = {}
        snapshot, old = read_revision(git, shas[0], policy, blobs)
        assert {source.path: source.text for source in old} == first_commit_files
        assert (snapshot.ref, snapshot.sha, len(blobs)) == (shas[0], shas[0][:7], 2)
        _, head = read_revision(git, "HEAD", policy, blobs)
        # not generated or huge
        assert sorted(source.path for source in head) == ["src/a.py", "src/gone.py", "tests/test_a.py"]
        # the unchanged test file is read once
        assert len(blobs) == 4
        assert git.read_blobs.call_args_list == [
            call(
                [
                    MemoryGit.blob_id(first_commit_files["src/a.py"]),
                    MemoryGit.blob_id(first_commit_files["tests/test_a.py"]),
                ]
            ),
            call(
                [
                    MemoryGit.blob_id(second_commit_files["src/a.py"]),
                    MemoryGit.blob_id(second_commit_files["src/gone.py"]),
                ]
            ),
        ]


class TestRate:
    """The rating itself: a pure function of a snapshot's files and their knowledge graph."""

    @pytest.mark.parametrize(
        ("source_files", "graph"), [(lf("api_and_core_sources"), lf("api_and_core_graph"))], indirect=True
    )
    def test_rates_every_module(
        self,
        source_files,
        graph,
        policy,
        today,
        demo_snapshot,
    ):
        rated = rate(demo_snapshot, source_files, graph, policy, today, None, None, 2)  # no coverage or cache; depth 2
        assert (rated.repo, rated.ref, rated.sha, rated.generated_at) == ("demo", "main", "abc1234", today)
        assert rated.policy_version == 2
        # largest first; tests are not modules
        assert [module.name for module in rated.modules] == ["src/api", "src/core"]
        api, core = rated.modules
        assert api.facts is not None
        assert core.facts is not None
        assert (api.loc, api.tests.functions, api.tests.tested, api.tests.scales) == (600, 1, 1, {"unit": 1})
        assert api.baseline.model_dump() == api.scores.model_dump(exclude={"overall"})
        assert (api.facts.funcs, api.facts.typed_share) == ([(2, 1)], 1.0)  # read from the source text
        assert core.facts.effect_counts == {"db_write": 2}
        assert core.facts.effect_examples == {"db_write": "src/core/c.py:2"}
        assert core.facts.fan_in == 1
        assert rated.notes[0].startswith("Knowledge graph (fake): 2 nodes")
        assert rated.loc_total == 700

    @pytest.mark.parametrize(
        ("source_files", "graph", "policy"),
        [(lf("api_and_core_sources"), lf("api_and_core_graph"), {"exclude": ["src/core/*"]})],
        indirect=True,
    )
    def test_excluded_code_is_not_rated(
        self,
        source_files,
        graph,
        policy,
        today,
        demo_snapshot,
    ):
        names = [
            module.name for module in rate(demo_snapshot, source_files, graph, policy, today, None, None, 2).modules
        ]
        assert names
        assert not any("core" in name for name in names), names

    @pytest.mark.parametrize(
        ("source_files", "graph"),
        [([], lf("api_and_core_graph")), ([lf("unit_test_file")], lf("api_and_core_graph"))],
        indirect=True,
        ids=["no files", "tests only"],
    )
    def test_no_python_source_to_rate(
        self,
        source_files,
        graph,
        policy,
        today,
        demo_snapshot,
    ):
        with pytest.raises(NoPythonSource):
            rate(demo_snapshot, source_files, graph, policy, today)


class TestRateWorkingTree:
    """A working tree is read through Git and the FileSystem, and rated with the built-in graph and the line coverage of
    its CoverageReport."""

    @pytest.mark.parametrize(
        ("history", "files", "coverage"),
        [(lf("core_history"), lf("core_checkout"), lf("core_line_rates"))],
        indirect=True,
    )
    def test_with_the_builtin_graph_and_coverage(
        self,
        git,
        fs,
        coverage,
        clock,
        policy,
        today,
    ):
        rated = rate_working_tree(git, fs, coverage, clock, policy)
        coverage.line_rates.assert_called_once_with("/repo")
        assert (rated.generated_at, [module.name for module in rated.modules]) == (today, ["src"])
        module_facts = rated.modules[0].facts
        assert module_facts is not None
        assert (module_facts.line_rate, rated.modules[0].tests.tested) == (0.5, 1)
        assert rated.notes[0].startswith("Knowledge graph (built-in ast)")

    @pytest.mark.parametrize(("history", "files"), [(lf("docs_only_history"), lf("docs_only_checkout"))], indirect=True)
    def test_without_python_source(
        self,
        git,
        fs,
        coverage,
        clock,
        policy,
    ):
        with pytest.raises(NoPythonSource):
            rate_working_tree(git, fs, coverage, clock, policy)


class TestRateRevision:
    """A revision is read from git objects alone; a RatingCache shares the work across revisions."""

    @pytest.mark.parametrize("history", [lf("two_commit_core_history")], indirect=True)
    def test_revisions_share_their_work_through_the_cache(
        self,
        git,
        clock,
        policy,
        rating_cache,
        shas,
        today,
        core_source,
        core_test_source,
        added_module_source,
    ):
        first, second = shas
        old = rate_revision(git, clock, first, policy, rating_cache)
        assert (old.ref, old.sha, old.generated_at, old.modules[0].files) == (first, first[:7], today, 1)
        assert len(rating_cache.blobs) == 2
        rate_revision(git, clock, second, policy, rating_cache)
        # the unchanged files are read and parsed once
        assert (len(rating_cache.blobs), len(rating_cache.syntax)) == (3, 3)
        assert git.read_blobs.call_args_list == [
            call([MemoryGit.blob_id(core_source), MemoryGit.blob_id(core_test_source)]),
            call([MemoryGit.blob_id(added_module_source)]),
        ]

    @pytest.mark.parametrize("history", [lf("docs_only_history")], indirect=True)
    def test_without_python_source(
        self,
        git,
        clock,
        policy,
        rating_cache,
    ):
        with pytest.raises(NoPythonSource):
            rate_revision(git, clock, "HEAD", policy, rating_cache)


class TestSummarizeHeatmap:
    """Summarizing a heat map: the reviewer's levels are applied first, then the ReportPresenter reports on it."""

    @pytest.mark.parametrize(
        ("report_presenter", "judgement"),
        # a report that says how many modules were judged when it was asked (str.format reads them off the heat map)
        [({"report.side_effect": "{0.judged_modules} judged".format}, lf("critical_business_levels"))],
        indirect=True,
    )
    def test_levels_are_applied_before_summarizing(
        self,
        report_presenter,
        heatmap,
        judgement,
        policy,
    ):
        final, text = summarize_heatmap(report_presenter, heatmap, judgement, policy)
        assert (text, final.judged_modules) == ("1 judged", 1)
        assert final.modules[0].scores.biz >= 75
        report_presenter.report.assert_called_once_with(final)


@pytest.mark.parametrize(
    ("files", "diagram_presenter"), [({lf("readme_path"): "# Demo\n"}, lf("diagram_replies"))], indirect=True
)
class TestUpdateReadme:
    """Refreshing the README's heat-map section and its diagram: written when the ratings change, and not otherwise."""

    def test_the_first_heat_map_is_added(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
        readme_path,
        svg_path,
    ):
        first = update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy)
        assert (first.changed, first.action) == (True, "ADD")
        assert (
            "![map](docs/risk-heatmap.svg)\n<!-- risk-heat-map:fingerprint=%s -->" % first.fingerprint
            in memory_fs.read_text(readme_path)
        )
        assert memory_fs.read_text(svg_path) == "<svg/>"

    def test_the_same_ratings_write_nothing(
        self,
        fs,
        diagram_presenter,
        heatmap,
        policy,
        readme_path,
        svg_path,
    ):
        first = update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy)
        again = update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy)
        assert not again.changed
        assert again.fingerprint == first.fingerprint
        assert fs.write_text.call_count == 2  # the diagram and the README, by the first update only

    def test_a_deleted_diagram_is_rewritten_though_the_ratings_match(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
        readme_path,
        svg_path,
    ):
        update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy)
        memory_fs.remove(svg_path)
        assert update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy).changed
        assert memory_fs.read_text(svg_path) == "<svg/>"

    def test_moved_ratings_update_the_section(
        self,
        fs,
        diagram_presenter,
        heatmap,
        policy,
        readme_path,
        svg_path,
    ):
        update_readme(fs, diagram_presenter, readme_path, svg_path, heatmap, None, policy)
        moved = heatmap.model_copy(deep=True)
        moved.modules[0].scores.biz = 90
        update = update_readme(fs, diagram_presenter, readme_path, svg_path, moved, None, policy)
        assert (update.changed, update.action) == (True, "UPDATE")

    def test_the_image_is_linked_relative_to_the_readme(
        self,
        fs,
        memory_fs,
        diagram_presenter,
        heatmap,
        policy,
        svg_path,
    ):
        update_readme(fs, diagram_presenter, "/repo/docs/README.md", svg_path, heatmap, None, policy)
        text = memory_fs.read_text("/repo/docs/README.md")
        assert "![map](risk-heatmap.svg)" in text
        assert text.endswith("\n")


class TestAgentJudgement:
    """The levels the agent wrote are read redacted, entry by entry; without a JSON object, the static levels stay."""

    @pytest.mark.parametrize("files", [{"/out/agent/judgement.json": lf("agent_levels")}], indirect=True)
    @pytest.mark.parametrize("environ", [{"GH_TOKEN": lf("gh_token")}], indirect=True)
    def test_the_levels_are_read_redacted(
        self,
        fs,
        memory_fs,
        env,
        run_log,
        run_folders,
        gh_token,
    ):
        judgement = agent_judgement(fs, env, run_log, run_folders)
        assert judgement is not None
        assert list(judgement.modules["m"]) == [Aspect.BUSINESS]  # the invalid entry is dropped
        assert judgement.modules["m"][Aspect.BUSINESS].reason == "read [REDACTED]"
        assert gh_token not in memory_fs.read_text("/out/agent/judgement.json")
        run_log.warning.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "warning"),
        [
            (lf("empty_agent_folder"), "the agent wrote no judgement.json"),
            ({"/out/agent/judgement.json": "[1, 2]"}, "not a JSON object"),
            ({"/out/agent/judgement.json": "{not json"}, "not a JSON object"),
        ],
        indirect=["files"],
        ids=["none", "not an object", "not JSON"],
    )
    def test_without_a_judgement_the_static_levels_stay(
        self,
        fs,
        env,
        run_log,
        run_folders,
        warning,
    ):
        assert agent_judgement(fs, env, run_log, run_folders) is None
        assert warning in run_log.warning.call_args.args[0]
