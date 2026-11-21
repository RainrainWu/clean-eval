"""CoverageReader over a Cobertura coverage.xml in a working tree, when there is one (revisions have none)."""
import os
import re
from pathlib import Path

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')


class CoverageXmlReader:
    def line_rates(self, snapshot):
        root = Path(snapshot.root)
        for report in sorted(root.glob("coverage.xml")) + sorted(root.glob("*/coverage.xml")):
            if os.path.islink(report):
                continue
            return {m.group(1): float(m.group(2)) for m in CLASS.finditer(report.read_text(errors="ignore"))}
        return {}


class NoCoverage:
    def line_rates(self, snapshot):
        return {}
