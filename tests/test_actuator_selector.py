import unittest
from unittest.mock import patch

from core.actuator_selector import choose_actuator


class TestActuatorSelector(unittest.TestCase):
    def test_wayland_fails_closed_instead_of_claiming_xdotool(self):
        with patch("core.actuator_selector.shutil.which", return_value="/usr/bin/xdotool"):
            choice = choose_actuator({"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"})
        self.assertFalse(choice.available)
        self.assertTrue(choice.requires_hil)
        self.assertIn("Wayland", choice.reason)

    def test_x11_requires_xdotool(self):
        with patch("core.actuator_selector.shutil.which", return_value=None):
            choice = choose_actuator({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":99"})
        self.assertFalse(choice.available)
        self.assertEqual(choice.backend, "none")

    def test_x11_selects_xdotool_only_when_present(self):
        with patch("core.actuator_selector.shutil.which", return_value="/usr/bin/xdotool"):
            choice = choose_actuator({"XDG_SESSION_TYPE": "x11", "DISPLAY": ":99"})
        self.assertTrue(choice.available)
        self.assertEqual(choice.backend, "xdotool-x11")
        self.assertFalse(choice.requires_hil)


if __name__ == "__main__":
    unittest.main()
