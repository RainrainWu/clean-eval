"""CoverageReader over a Cobertura coverage.xml in a working tree, when there is one (revisions have none). Reads it
through the file-system gateway."""
import re

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')


class CoverageXmlReader:
    def __init__(self, fs):
        self.fs = fs

    def line_rates(self, snapshot):
        for report in self.fs.glob(snapshot.root, "coverage.xml") + self.fs.glob(snapshot.root, "*/coverage.xml"):
            if self.fs.is_link(report):
                continue
            return {m.group(1): float(m.group(2)) for m in CLASS.finditer(self.fs.read_text(report))}
        return {}


class NoCoverage:
    def line_rates(self, snapshot):
        return {}
