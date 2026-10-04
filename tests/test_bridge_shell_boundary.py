import ast
import pathlib
import unittest

from core.bridge_to_eidos import _safe_shell_argv

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestBridgeShellBoundary(unittest.TestCase):
    def test_simple_explicit_command_becomes_argv(self):
        argv, reason = _safe_shell_argv("git status --short")
        self.assertEqual(argv, ["git", "status", "--short"])
        self.assertEqual(reason, "")

    def test_shell_composition_is_rejected(self):
        for cmd in (
            "echo ok | sh",
            "echo ok && touch /tmp/eidos-injected",
            "echo $(id)",
            "echo ok > /tmp/eidos-output",
            "printf ok; id",
        ):
            argv, reason = _safe_shell_argv(cmd)
            self.assertIsNone(argv, cmd)
            self.assertTrue(reason, cmd)

    def test_destructive_binary_is_rejected(self):
        argv, reason = _safe_shell_argv("wipefs /dev/sda")
        self.assertIsNone(argv)
        self.assertIn("bloqueado", reason.lower())

    def test_bridge_never_executes_model_shell_directives(self):
        tree = ast.parse((ROOT / "core" / "bridge_to_eidos.py").read_text(encoding="utf-8"))
        shell_true = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        shell_true.append(node.lineno)
        self.assertEqual(shell_true, [], f"shell=True remains at lines {shell_true}")

        source = (ROOT / "core" / "bridge_to_eidos.py").read_text(encoding="utf-8")
        self.assertIn("Propuesta de shell del modelo no ejecutada", source)


if __name__ == "__main__":
    unittest.main()
