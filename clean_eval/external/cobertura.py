"""Cobertura's XML coverage report (coverage.xml, as coverage.py and pytest-cov write it): the line coverage a working
tree's tests measured. It is the use cases' CoverageReport interface, and reads through the FileSystem gateway: the
report is read as text, at the working tree's root or one level below it, never through a symlink."""

import re

from clean_eval.use_cases.interfaces import (
    CoverageReport,
    FileSystem,
)

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')  # a source file's line rate


class CoberturaGateway(CoverageReport):
    def __init__(
        self,
        fs: FileSystem,
    ) -> None:
        self.fs = fs  # finds and reads the report

    def line_rates(
        self,
        root: str,
    ) -> dict[str, float]:
        fs = self.fs
        for report in fs.glob(root, "coverage.xml") + fs.glob(root, "*/coverage.xml"):
            if fs.is_link(report):  # it could point outside the working tree
                continue
            return {match.group(1): float(match.group(2)) for match in CLASS.finditer(fs.read_text(report))}
        return {}
