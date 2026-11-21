import contextlib
import io
import json
import os
import tempfile
import unittest

from helpers import Repo, days_ago
import risk


def model(**overrides):
    m = risk.load_model(risk.DEFAULT_MODEL)
    m.update(overrides)
    return m


class UnquotePathTest(unittest.TestCase):
    def test_plain_path_unchanged(self):
        self.assertEqual(risk.unquote_path("src/a.py"), "src/a.py")

    def test_octal_utf8(self):
        self.assertEqual(risk.unquote_path('"src/r\\303\\251sum\\303\\251.py"'), "src/résumé.py")

    def test_escaped_quote_and_backslash(self):
        self.assertEqual(risk.unquote_path('"src/we\\"ird\\\\x.py"'), 'src/we"ird\\x.py')

    def test_tab(self):
        self.assertEqual(risk.unquote_path('"a\\tb"'), "a\tb")


class ModuleMapTest(unittest.TestCase):
    def setUp(self):
        self.mm = risk.ModuleMap(model(), ["src/billing/a.py", "src/users/b.py", "scripts/x.sh", "README.md"])

    def test_roots_and_depth(self):
        self.assertEqual(self.mm.module_of("src/billing/a.py"), "src/billing")
        self.assertEqual(self.mm.module_of("src/billing/deep/c.py"), "src/billing")
        self.assertEqual(self.mm.module_of("src/top.py"), "src")
        self.assertEqual(self.mm.module_of("scripts/x.sh"), "scripts")

    def test_exclude_and_include(self):
        self.assertIsNone(self.mm.module_of("README.md"))
        self.assertIsNone(self.mm.module_of("docs/guide.txt"))
        self.assertEqual(self.mm.module_of(".github/workflows/ci.yml"), ".github")  # include wins
        self.assertEqual(self.mm.module_of(".claude/skills/x/SKILL.md"), ".claude")  # include beats *.md
        self.assertIsNone(self.mm.module_of(".vscode/settings.json"))

    def test_top_level_tests_attributed_by_name(self):
        self.assertTrue(self.mm.is_test("tests/test_billing.py"))
        self.assertEqual(self.mm.module_of("tests/test_billing.py", {"src/billing", "src/users"}), "src/billing")
        self.assertIsNone(self.mm.module_of("tests/test_unrelated.py", {"src/billing"}))

    def test_explicit_modules_override(self):
        mm = risk.ModuleMap(model(modules={"payments": ["src/billing/*", "src/invoices/*"]}), ["src/billing/a.py"])
        self.assertEqual(mm.module_of("src/invoices/i.py"), "payments")


class ComputeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = r = Repo()
        r.commit("feat: billing", {"src/billing/a.py": "x\n" * 50}, when=days_ago(40))
        r.commit("fix: billing rounding", {"src/billing/a.py": "y\n" * 50}, when=days_ago(30))
        r.commit("fix: billing tax", {"src/billing/a.py": "z\n" * 50, "src/users/u.py": "u\n" * 10}, when=days_ago(20))
        r.commit("test: billing", {"tests/test_billing.py": "t\n" * 30}, when=days_ago(10))
        cls.result = risk.compute(r.path, model())
        cls.by = {m["module"]: m for m in cls.result["modules"]}

    @classmethod
    def tearDownClass(cls):
        cls.repo.cleanup()

    def test_modules_detected(self):
        self.assertEqual(set(self.by), {"src/billing", "src/users"})

    def test_signals(self):
        b, u = self.by["src/billing"], self.by["src/users"]
        self.assertEqual(b["commits"], 3)
        self.assertEqual(b["fix_commits"], 2)
        self.assertAlmostEqual(b["signals"]["fix_ratio"], round(2 / 5, 3))  # fix / (commits + prior 2)
        self.assertEqual(b["test_loc"], 30)                                 # tests/test_billing.py attributed
        self.assertEqual(u["signals"]["test_gap"], 1.0)
        self.assertAlmostEqual(u["signals"]["coupling"], round(1 / 3, 3))   # 1 co-change / (1 + 2)
        self.assertEqual(b["signals"]["churn"], 1.0)                        # busiest module

    def test_classify_new_module_gets_new_module_tier(self):
        c = risk.classify(self.repo.path, model(), ["src/billing/a.py", "src/brand_new/x.py", "README.md"],
                          result=self.result)
        mods = {m["module"]: m for m in c["modules"]}
        self.assertTrue(mods["src/brand_new"]["new_module"])
        self.assertEqual(mods["src/brand_new"]["tier"], "Medium")
        self.assertIsNone(c["files"]["README.md"])
        self.assertIn(c["max_tier"], risk.TIER_ORDER[1:])

    def test_score_module_takes_criticality_from_model(self):
        sig = self.by["src/users"]["signals"]
        low = risk.score_module("src/users", sig, model(criticality={"src/users": 0.0}))
        high = risk.score_module("src/users", sig, model(criticality={"src/users": 1.0}))
        self.assertLess(low["impact"], high["impact"])
        self.assertEqual(high["signals"]["criticality"], 1.0)


class QuotedPathTest(unittest.TestCase):
    def test_non_ascii_and_quote_paths_map_to_their_module(self):
        r = Repo()
        try:
            base = r.commit("init", {"src/pay/ok.py": "a\n"}, when=days_ago(5))
            head = r.commit("feat: names", {"src/pay/résumé.py": "b\n", 'src/pay/we"ird.py': "c\n"}, when=days_ago(4))
            res = risk.compute(r.path, model())
            self.assertEqual([m["module"] for m in res["modules"]], ["src/pay"])
            self.assertEqual(res["modules"][0]["commits"], 2)
            files = risk.diff_files(r.path, base, head)
            self.assertEqual(sorted(files), sorted(["src/pay/résumé.py", 'src/pay/we"ird.py']))
            c = risk.classify(r.path, model(), files, rev=base)
            self.assertEqual({m["module"] for m in c["modules"]}, {"src/pay"})
        finally:
            r.cleanup()


class WriteReadmeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.readme = os.path.join(self.dir, "README.md")

    def block(self, body, date="2026-01-01", sha="abc"):
        return "\n".join([risk.START_MARK, "", f"{risk.GENERATED_PREFIX} · as of {date} · commit `{sha}`_", "",
                          body, "", risk.END_MARK])

    def test_creates_section_in_new_file(self):
        self.assertTrue(risk.write_readme(self.readme, self.block("table v1")))
        self.assertIn("## Risk Heat Map", risk.read_text(self.readme))

    def test_unchanged_when_only_generated_line_differs(self):
        risk.write_readme(self.readme, self.block("table v1"))
        before = risk.read_text(self.readme)
        self.assertFalse(risk.write_readme(self.readme, self.block("table v1", date="2027-02-02", sha="def")))
        self.assertEqual(risk.read_text(self.readme), before)

    def test_changed_body_rewrites_between_markers(self):
        risk.write_text(self.readme, "# T\n\n## Risk Heat Map\n\nold\n\n## Next\nkeep\n")
        self.assertTrue(risk.write_readme(self.readme, self.block("table v1")))
        self.assertTrue(risk.write_readme(self.readme, self.block("table v2")))
        text = risk.read_text(self.readme)
        self.assertIn("table v2", text)
        self.assertNotIn("table v1", text)
        self.assertNotIn("old", text)
        self.assertTrue(text.rstrip().endswith("keep"))
        self.assertEqual(text.count(risk.START_MARK), 1)


class CliTest(unittest.TestCase):
    def test_status_json_and_classify_diff(self):
        r = Repo()
        try:
            base = r.commit("init", {"src/a/x.py": "1\n"}, when=days_ago(3))
            head = r.commit("feat", {"src/b/y.py": "2\n"}, when=days_ago(2))
            status = os.path.join(r.path, "..", os.path.basename(r.path) + "-status.json")
            quiet = contextlib.redirect_stdout(io.StringIO())
            with quiet, contextlib.redirect_stderr(io.StringIO()):
                risk.main(["--repo", r.path, "heatmap", "--write-readme", "--status-json", status])
            self.assertEqual(risk.read_json(status), {"readme_changed": True, "modules": 2})
            r.commit("docs: heat map")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                risk.main(["--repo", r.path, "heatmap", "--write-readme", "--status-json", status])
            self.assertFalse(risk.read_json(status)["readme_changed"])
            os.remove(status)
        finally:
            r.cleanup()


if __name__ == "__main__":
    unittest.main()
