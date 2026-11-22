"""Line coverage from a Cobertura coverage.xml in a working tree, when there is one (revisions have none), through the
FileSystem interface."""
import re

CLASS = re.compile(r'<class [^>]*filename="([^"]+)"[^>]*line-rate="([0-9.]+)"')


def coverage_line_rates(fs, root):
    """{file path: line rate 0-1} from the first coverage.xml at or below `root` (one level), or {}."""
    for report in fs.glob(root, "coverage.xml") + fs.glob(root, "*/coverage.xml"):
        if fs.is_link(report):
            continue
        return {m.group(1): float(m.group(2)) for m in CLASS.finditer(fs.read_text(report))}
    return {}
