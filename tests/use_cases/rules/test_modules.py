"""Splitting a repository into modules (use_cases/rules/modules.py), and finding the module of a path."""
import unittest

from tests.domain.builders import sf

from clean_eval.domain.policy import Policy
from clean_eval.use_cases.rules.modules import module_of, split_modules

P = Policy()


class Modules(unittest.TestCase):
    def test_large_module_is_split_into_children(self):
        mods, notes = split_modules([sf("a/b/x.py", 400), sf("a/c/y.py", 400), sf("z/w.py", 100)], P, depth=2)
        self.assertEqual(sorted(mods), ["a/b", "a/c", "z"])
        self.assertTrue(any("split a" in n for n in notes))

    def test_single_child_chain_is_skipped(self):
        mods, _ = split_modules([sf("pkg/src/pkg/one/a.py", 300), sf("pkg/src/pkg/two/b.py", 300), sf("other/c.py", 50)], P, depth=2)
        self.assertIn("pkg/src/pkg/one", mods)
        self.assertIn("pkg/src/pkg/two", mods)

    def test_depth_and_cap(self):
        files = [sf("src/ext/a.py", 100), sf("src/ext/b.py", 100), sf("src/ext/c.py", 100), sf("src/dom/d.py", 60), sf("src/dom/e.py", 60)]
        self.assertEqual(sorted(split_modules(files, P, depth=1)[0]), ["src"])
        self.assertEqual(sorted(split_modules(files, P, depth=2)[0]), ["src/dom", "src/ext"])
        self.assertEqual(sorted(split_modules(files, P)[0]), ["src/dom", "src/ext/a.py", "src/ext/b.py", "src/ext/c.py"])
        self.assertEqual(sorted(split_modules(files, P, depth=3, max_modules=2)[0]), ["src/dom", "src/ext"])

    def test_module_of_finds_the_deepest_module(self):
        names = {"src/api", "src/ext/a.py", "src/core/(files)", "src/core/deep", "(root)", "tools"}
        self.assertEqual(module_of("src/api/handlers/x.py", names), "src/api")
        self.assertEqual(module_of("src/ext/a.py", names), "src/ext/a.py")
        self.assertIsNone(module_of("src/ext/new.py", names))  # a new file in a folder split per file
        self.assertEqual(module_of("src/core/c.py", names), "src/core/(files)")
        self.assertEqual(module_of("src/core/deep/d.py", names), "src/core/deep")
        self.assertEqual(module_of("setup.py", names), "(root)")
        self.assertEqual(module_of("tools/x/y.sh", names), "tools")
        self.assertIsNone(module_of("terraform/main.tf", names))
        self.assertIsNone(module_of("setup.py", {"src"}))


if __name__ == "__main__":
    unittest.main()
