import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.dynamic_tools as dynamic_tools


class TestDynamicToolPromotion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(dynamic_tools, "TOOLS_DIR", root / "tools"),
            patch.object(dynamic_tools, "DB_PATH", root / "tools.db"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.builder = dynamic_tools.DynamicToolBuilder()

    def tearDown(self):
        self.builder.db.close()
        self.tmp.cleanup()

    def test_unsafe_candidate_is_not_written_or_registered(self):
        with patch.object(
            self.builder, "_validate_safety",
            return_value=(False, ["unsafe-test"]),
        ):
            result = self.builder.build_tool("parse json", name="unsafe_candidate")
        self.assertFalse(result.success)
        self.assertFalse((dynamic_tools.TOOLS_DIR / "unsafe_candidate.py").exists())
        row = self.builder.db.execute(
            "SELECT active FROM tools WHERE name=?", ("unsafe_candidate",)
        ).fetchone()
        self.assertIsNone(row)

    def test_failed_smoke_test_is_inactive(self):
        with patch.object(
            self.builder, "_test_in_sandbox",
            return_value=(False, "synthetic failure"),
        ):
            result = self.builder.build_tool("parse json", name="failed_smoke")
        self.assertFalse(result.success)
        row = self.builder.db.execute(
            "SELECT active FROM tools WHERE name=?", ("failed_smoke",)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["active"], 0)


if __name__ == "__main__":
    unittest.main()
