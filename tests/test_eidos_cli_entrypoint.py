import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestEidosCliEntrypoint(unittest.TestCase):
    def test_no_arg_default_is_shared_cli(self):
        source = (ROOT / "eidos.py").read_text(encoding="utf-8")
        self.assertIn('default="cli"', source)
        self.assertIn('"cli": cmd_cli', source)
        self.assertIn("SharedAgentsTerminal", source)

    def test_terminal_and_runtime_share_capability_catalog(self):
        source = (ROOT / "bin" / "eidos_agents_terminal.py").read_text(encoding="utf-8")
        self.assertIn("self.hub.capability_catalog()", source)
        self.assertNotIn("TOOL_HELP =", source)
        self.assertNotIn("CAPABILITIES =", source)


if __name__ == "__main__":
    unittest.main()
