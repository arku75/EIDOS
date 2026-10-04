import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hardware_validation.sh"


class TestHardwareValidationScript(unittest.TestCase):
    def test_script_exists_and_is_read_only_by_default(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('MODE="dry"', text)
        self.assertIn('active_checks=skipped', text)
        self.assertNotIn("systemctl --user restart", text)
        self.assertNotIn("systemctl --user start", text)
        self.assertNotIn("ollama pull", text)
        self.assertNotIn("xdotool click", text)
        self.assertNotIn("xdotool key", text)
        self.assertNotIn("rm -rf", text)

    def test_active_mode_stays_non_destructive(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('MODE="active"', text)
        self.assertIn("No mouse, keyboard, browser-profile, DB write, service restart, model download or network bind was performed.", text)


if __name__ == "__main__":
    unittest.main()
