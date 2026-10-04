import ast
import os
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestBrowserSecurityContract(unittest.TestCase):
    def test_playwright_has_no_shell_true_or_implicit_mozilla_profile_clone(self):
        src = (ROOT / "core/eidos_playwright.py").read_text()
        tree = ast.parse(src)
        self.assertNotIn("~/.mozilla/firefox", src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                        self.assertIsNot(kw.value.value, True)

    def test_browser_manager_has_no_hardcoded_user_profile(self):
        src = (ROOT / "core/browser_manager.py").read_text()
        self.assertNotIn("yii3m1ky.default-esr", src)
        self.assertIn("EIDOS_FIREFOX_PROFILE", src)

    def test_session_navigation_fails_closed_without_explicit_profile(self):
        import core.browser_manager as bm
        with patch.object(bm, "FIREFOX_PROFILE", ""):
            out = bm.navigate_with_session("https://example.invalid")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "session_profile_not_authorized")


if __name__ == "__main__":
    unittest.main()
