import types
import unittest
from unittest.mock import MagicMock, patch

from core import causal_loop


def element(*, from_sc=False):
    return types.SimpleNamespace(text="Safe button", x=10, y=20, from_sc=from_sc)


class TestCausalLoopSafety(unittest.TestCase):
    def tearDown(self):
        causal_loop._SCREEN_CONTROLLER = None

    def test_screen_controller_cannot_bypass_unknown_operator_presence(self):
        fake_sc = MagicMock()
        with patch.object(causal_loop, "_get_sc", return_value=fake_sc), \
             patch("subprocess.run", side_effect=FileNotFoundError("xprintidle missing")), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("EIDOS_MASTER_MODE", None)
            self.assertFalse(causal_loop._act(element(from_sc=True), dry_run=False))
        fake_sc.human_emulator.click_at.assert_not_called()

    def test_idle_operator_blocks_screen_controller(self):
        fake_sc = MagicMock()
        proc = types.SimpleNamespace(stdout="300001\n")
        with patch.object(causal_loop, "_get_sc", return_value=fake_sc), \
             patch("subprocess.run", return_value=proc), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("EIDOS_MASTER_MODE", None)
            self.assertFalse(causal_loop._act(element(from_sc=True), dry_run=False))
        fake_sc.human_emulator.click_at.assert_not_called()

    def test_act_does_not_credit_motor_success_before_verification(self):
        import inspect
        source = inspect.getsource(causal_loop._act)
        self.assertNotIn("remember_motor", source)
        self.assertNotIn("success=True", source)

    def test_body_check_exception_fails_closed_before_webpanel(self):
        proc = types.SimpleNamespace(stdout="0\n")
        with patch("subprocess.run", return_value=proc), \
             patch("core.body.hand_ok", side_effect=RuntimeError("body unavailable")), \
             patch("urllib.request.urlopen") as urlopen, \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("EIDOS_MASTER_MODE", None)
            self.assertFalse(causal_loop._act(element(), dry_run=False))
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
