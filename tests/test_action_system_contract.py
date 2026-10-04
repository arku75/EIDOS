import unittest
from unittest.mock import patch

from core import action_system


class TestActionSystem(unittest.TestCase):
    def test_sequence_stops_on_false_action_result(self):
        with patch.object(action_system, "_PYAUTOGUI_AVAILABLE", True), \
             patch.object(action_system, "move_to", return_value=False), \
             patch.object(action_system, "click") as click:
            self.assertFalse(action_system.execute_sequence([
                {"type": "move", "x": 1, "y": 2},
                {"type": "click"},
            ]))
        click.assert_not_called()

    def test_unknown_action_is_failure(self):
        with patch.object(action_system, "_PYAUTOGUI_AVAILABLE", True):
            self.assertFalse(action_system.execute_sequence([
                {"type": "invented-action"}
            ]))

    def test_all_successful_actions_report_success(self):
        with patch.object(action_system, "_PYAUTOGUI_AVAILABLE", True), \
             patch.object(action_system, "move_to", return_value=True), \
             patch.object(action_system, "click", return_value=True):
            self.assertTrue(action_system.execute_sequence([
                {"type": "move", "x": 1, "y": 2},
                {"type": "click"},
            ]))


if __name__ == "__main__":
    unittest.main()
