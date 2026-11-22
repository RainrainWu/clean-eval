"""The Cobertura gateway (external/cobertura.py), over real files (`fs`, the file-system gateway): the line rates of
a working tree's coverage.xml, at its root or one level below, never through a symlink."""

import pytest
from pytest_lazy_fixtures import lf

from clean_eval.external.cobertura import CoberturaGateway


class TestLineRates:
    """The report at the root, else one level below it; none deeper, and none through a symlink."""

    @pytest.mark.parametrize(
        "files",
        [
            {"coverage.xml": lf("coverage_report"), "build/coverage.xml": lf("deeper_coverage_report")},
            {"build/coverage.xml": lf("coverage_report"), "deep/er/coverage.xml": lf("deeper_coverage_report")},
        ],
        indirect=True,
        ids=["at the root", "one level below"],
    )
    def test_the_report_nearest_the_root_is_read(
        self,
        fs,
        root,
    ):
        assert CoberturaGateway(
            fs,
        ).line_rates(root) == {"src/a.py": 0.75}

    @pytest.mark.parametrize("files", [{"deep/er/coverage.xml": lf("coverage_report")}], indirect=True)
    def test_a_report_two_levels_down_is_not_looked_at(
        self,
        fs,
        root,
    ):
        assert (
            CoberturaGateway(
                fs,
            ).line_rates(root)
            == {}
        )

    @pytest.mark.parametrize(
        ("files", "links"),
        [({"elsewhere.xml": lf("coverage_report")}, {"coverage.xml": "elsewhere.xml"})],
        indirect=True,
    )
    def test_a_symlinked_report_is_not_read(
        self,
        fs,
        root,
    ):
        assert (
            CoberturaGateway(
                fs,
            ).line_rates(root)
            == {}
        )
