"""The environment gateway (external/environment.py): variables, path lists, and copies rather than the live environment."""
import os
import unittest
from unittest import mock

from clean_eval.external.environment import EnvironmentGateway


class EnvironmentAndClockTest(unittest.TestCase):
    def test_environment(self):
        env = EnvironmentGateway()
        with mock.patch.dict(os.environ, {"CE_ROOTS": os.pathsep.join(["/a", "", "/b c"]), "CE_X": "1"}):
            self.assertEqual(env.paths("CE_ROOTS"), ["/a", "/b c"])
            self.assertEqual(env.paths("CE_UNSET"), [])
            self.assertEqual((env.get("CE_X"), env.get("CE_UNSET", "d")), ("1", "d"))
            snapshot = env.variables()
        snapshot["CE_X"] = "changed"
        self.assertNotEqual(os.environ.get("CE_X"), "changed")  # a copy, not the live environment


if __name__ == "__main__":
    unittest.main()
