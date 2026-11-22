"""Line coverage from a working tree's Cobertura coverage.xml (use_cases/coverage.py), through the FileSystem interface."""
import unittest

from tests.use_cases.fakes import MemoryFileSystem

from clean_eval.use_cases.coverage import coverage_line_rates

REPORT = '<coverage><class name="a" filename="src/a.py" line-rate="0.75"/></coverage>'


class CoverageTest(unittest.TestCase):
    def test_the_report_at_or_one_level_below_the_root(self):
        fs = MemoryFileSystem({"/repo/deep/er/coverage.xml": REPORT})
        self.assertEqual(coverage_line_rates(fs, "/repo"), {})  # two levels down is not looked at
        fs.write_text("/repo/build/coverage.xml", REPORT)
        self.assertEqual(coverage_line_rates(fs, "/repo"), {"src/a.py": 0.75})

    def test_a_symlinked_report_is_not_read(self):
        fs = MemoryFileSystem({"/elsewhere.xml": REPORT})
        fs.link("/repo/coverage.xml", "/elsewhere.xml")
        self.assertEqual(coverage_line_rates(fs, "/repo"), {})


if __name__ == "__main__":
    unittest.main()
