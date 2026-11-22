"""Reading code (use_cases/rules/analysis.py): classifying paths, splitting modules, graph queries, per-file
signals, and the built-in knowledge graph."""

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.use_cases.rules import analysis  # analysis.test_files_to_modules by its module, or pytest collects it
from clean_eval.use_cases.rules.analysis import (
    builtin_graph,
    file_signals,
    module_of,
    python_stats,
    split_modules,
    write_calls,
)


class TestClassifyPaths:
    """What a path is: a test and its scale, code not rated for Unverified (manifests, test code), a core-flow name,
    tooling, a migration."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [("tests/unit/test_x.py", True), ("src/conftest.py", True), ("src/testing_utils.py", False)],
    )
    def test_a_test_by_its_folder_or_its_name(
        self,
        policy,
        path,
        expected,
    ):
        assert analysis.is_test(path, policy) is expected

    @pytest.mark.parametrize("scale", ["e2e", "integration", "unit"])
    def test_a_tests_folder_names_its_scale(
        self,
        scale,
    ):
        assert analysis.classify_scale("tests/%s/test_a.py" % scale) == scale

    @pytest.mark.parametrize("name", ["terraform/lambda_src", ".github/scripts", "k8s/overlays", "src/testing"])
    def test_manifests_and_test_code_are_not_applicable(
        self,
        policy,
        name,
    ):
        assert analysis.not_applicable(name, policy) is not None

    def test_source_code_is_applicable(
        self,
        policy,
    ):
        assert analysis.not_applicable("src/api", policy) is None

    @pytest.mark.parametrize(("name", "expected"), [("src/ingestion", True), ("src/helpers", False)])
    def test_a_core_flow_keyword_in_the_name(
        self,
        policy,
        name,
        expected,
    ):
        assert analysis.has_core_keyword(name, policy) is expected

    def test_a_scripts_folder_is_tooling(
        self,
        policy,
    ):
        assert analysis.looks_like_tooling("scripts/x", "scripts/x/a.py", policy)

    def test_an_alembic_version_is_a_migration(
        self,
        policy,
    ):
        assert analysis.is_migration("db/alembic/versions/001.py", policy)


class TestIgnoredAndExcludedPaths:
    """The changed paths that carry no risk of their own (documentation, images), and the source the policy leaves out
    of the ratings."""

    @pytest.mark.parametrize(
        "path", ["README.md", "pkg/README.rst", "CHANGELOG.md", "LICENSE", "docs/guide/x.py", "img/a.png"]
    )
    def test_docs_and_assets_are_ignored(
        self,
        policy,
        path,
    ):
        assert analysis.ignored(path, policy)

    @pytest.mark.parametrize(
        "path",
        [
            "requirements.txt",
            "SKILL.md",
            ".claude/skills/x/SKILL.md",
            "CLAUDE.md",
            "AGENTS.md",
            ".clean-eval/rules/quality.md",
        ],
    )
    def test_dependencies_agent_instructions_and_review_rules_carry_risk(
        self,
        policy,
        path,
    ):
        assert not analysis.ignored(path, policy)  # they steer behaviour

    @pytest.mark.parametrize("policy", [{"exclude": ["vendor/*"]}], indirect=True)
    def test_the_policy_excludes_paths_by_glob(
        self,
        policy,
    ):
        assert analysis.excluded("vendor/lib/x.py", policy)
        assert not analysis.excluded("src/x.py", policy)


class TestSplitModules:
    """A repository split into modules: its top-level folders, then each large module into its sub-folders (or files),
    level by level down to a depth, and never past a cap on the number of modules."""

    @pytest.mark.parametrize("source_files", [lf("large_and_small_module_files")], indirect=True)
    @pytest.mark.parametrize("depth", [2])
    def test_a_large_module_is_split_into_its_children(
        self,
        source_files,
        policy,
        depth,
    ):
        modules, notes = split_modules(source_files, policy, depth)
        assert sorted(modules) == ["a/b", "a/c", "z"]
        assert any("split a" in note for note in notes)

    @pytest.mark.parametrize("source_files", [lf("src_layout_files")], indirect=True)
    @pytest.mark.parametrize("depth", [2])
    def test_a_single_child_chain_is_skipped(
        self,
        source_files,
        policy,
        depth,
    ):
        modules, _ = split_modules(source_files, policy, depth)
        assert "pkg/src/pkg/one" in modules
        assert "pkg/src/pkg/two" in modules

    @pytest.mark.parametrize("source_files", [lf("ext_and_dom_files")], indirect=True)
    @pytest.mark.parametrize(
        ("depth", "max_modules", "expected"),
        [
            (1, None, ["src"]),
            (2, None, ["src/dom", "src/ext"]),
            (None, None, ["src/dom", "src/ext/a.py", "src/ext/b.py", "src/ext/c.py"]),
            (3, 2, ["src/dom", "src/ext"]),
        ],
        ids=["depth 1", "depth 2", "the policy's depth and cap", "capped at 2 modules"],
    )
    def test_the_depth_and_the_cap(
        self,
        source_files,
        policy,
        depth,
        max_modules,
        expected,
    ):
        assert sorted(split_modules(source_files, policy, depth, max_modules)[0]) == expected


class TestModuleOf:
    """The rated module a path belongs to: the file itself when it is one, else the deepest folder module that holds
    it, else the root module for a top-level file."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("src/api/handlers/x.py", "src/api"),
            ("src/ext/a.py", "src/ext/a.py"),
            ("src/ext/new.py", None),
            ("src/core/c.py", "src/core/(files)"),
            ("src/core/deep/d.py", "src/core/deep"),
            ("setup.py", "(root)"),
            ("tools/x/y.sh", "tools"),
            ("terraform/main.tf", None),
        ],
        ids=[
            "in a folder module",
            "a file module",
            "a new file in a folder split per file",
            "a file of a split folder",
            "in a sub-folder module",
            "a top-level file",
            "any file of a folder module",
            "outside every module",
        ],
    )
    def test_the_deepest_module_that_holds_the_path(
        self,
        rated_module_names,
        path,
        expected,
    ):
        assert module_of(path, rated_module_names) == expected

    def test_a_top_level_file_has_none_without_a_root_module(
        self,
    ):
        assert module_of("setup.py", {"src"}) is None


@pytest.mark.parametrize("graph", [lf("three_files_graph")], indirect=True)
class TestGraphQueries:
    """What the knowledge graph says about modules: who depends on whom, their functions, and the test files linked to
    them."""

    def test_dependents(
        self,
        graph,
        three_files_modules,
    ):
        fan_in, fan_out = analysis.dependents(graph, three_files_modules)
        assert (fan_in["B"], fan_out["A"]) == ({"A"}, {"B"})

    def test_functions_by_module_leave_test_code_out(
        self,
        graph,
        three_files_modules,
    ):
        assert sorted(analysis.functions_by_module(graph, three_files_modules)) == ["A", "B"]

    def test_a_test_file_maps_to_the_modules_of_its_functions(
        self,
        graph,
        three_files_modules,
    ):
        assert analysis.test_files_to_modules(graph, three_files_modules) == {"tests/test_a.py": {"A"}}


class TestPythonStats:
    """What the syntax tree of a Python file shows: the size and complexity of its functions, their typing, and broad
    and swallowed exception handlers."""

    def test_functions_typing_and_exception_handlers_are_counted(
        self,
        typed_and_loose_functions,
    ):
        stats = python_stats(typed_and_loose_functions)
        assert stats is not None
        assert (len(stats.funcs), stats.typed, stats.broad, stats.swallow) == (2, 1, 1, 1)
        assert stats.funcs[0][1] == 3  # one if and one boolean operator on top of the base path

    def test_text_that_does_not_parse_has_none(
        self,
    ):
        assert python_stats("def broken(:") is None


class TestWriteCalls:
    """The write calls in a file, by kind, with their count and first line; a kind counts only in its context."""

    def test_writes_are_counted_with_their_first_line(
        self,
        aws_writes,
    ):
        assert write_calls(aws_writes)["aws_write"] == (2, 3)

    def test_a_call_outside_its_context_is_not_a_write(
        self,
    ):
        assert "aws_write" not in write_calls("x.put_object(1)\n")  # no boto import: not an AWS write


class TestFileSignals:
    """What one file says about risk, computed once per file content."""

    @pytest.mark.parametrize("source_files", [[lf("todo_file")]], indirect=True)
    def test_signals_are_cached_per_content(
        self,
        source_files,
        rating_cache,
    ):
        (source_file,) = source_files
        signals = file_signals(source_file, rating_cache.signals)
        assert signals.todo == 2
        renamed = source_file.model_copy(update={"path": "renamed.py", "text": "ignored"})  # the same content id
        assert file_signals(renamed, rating_cache.signals) is signals


@pytest.mark.parametrize("source_files", [lf("package_repository_files")], indirect=True)
class TestBuiltinGraph:
    """The built-in knowledge graph: imports resolved by path, and tests linked to the functions they import and name;
    each file content is parsed once."""

    def test_imports_resolve_by_path(
        self,
        source_files,
    ):
        built = builtin_graph(source_files)
        assert built.links["src/pkg/core.py"] == {"src/pkg/util.py"}  # relative; stdlib json skipped
        assert built.links["src/pkg/__init__.py"] == {"src/pkg/core.py"}
        assert built.links["tests/test_core.py"] == {"src/pkg/__init__.py", "src/pkg/core.py"}  # src layout
        assert built.links["lambdas/x/handler.py"] == {"lambdas/x/utils.py"}  # sibling, not other/deep
        assert built.stats["source"] == "built-in ast"

    def test_tests_link_to_the_functions_they_import_and_name(
        self,
        source_files,
    ):
        built = builtin_graph(source_files)
        assert built.tested["src/pkg/core.py::run"] == {"tests/test_core.py"}  # through the __init__ re-export
        assert built.tested["src/pkg/core.py::Svc.go"] == {"tests/test_core.py"}
        assert "src/pkg/util.py::helper" not in built.tested
        assert built.nodes["src/pkg/core.py::Svc.go"] == ("Function", "src/pkg/core.py", False)

    def test_per_file_syntax_is_cached(
        self,
        source_files,
        rating_cache,
        package_repository,
    ):
        builtin_graph(source_files, rating_cache.syntax)
        assert len(rating_cache.syntax) == len(package_repository)
        before = dict(rating_cache.syntax)
        builtin_graph(source_files, rating_cache.syntax)
        assert rating_cache.syntax == before
