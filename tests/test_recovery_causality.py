import unittest
from unittest.mock import patch

from core.eidos_recovery import ActionVerifier


class TestRecoveryCausality(unittest.TestCase):
    def setUp(self):
        self.verifier = ActionVerifier()

    def test_type_without_observable_target_is_unverified(self):
        with patch("core.eidos_recovery.time.sleep"):
            ok, detail = self.verifier.verify_type("hello")
        self.assertFalse(ok)
        self.assertEqual(detail, "unverified_no_target_coords")

    def test_key_dispatch_is_not_assumed_success(self):
        ok, detail = self.verifier.verify("key", {"key": "Enter"}, "before")
        self.assertFalse(ok)
        self.assertEqual(detail, "unverified_key_action")

    def test_unknown_action_fails_closed(self):
        ok, detail = self.verifier.verify("teleport", {}, "before")
        self.assertFalse(ok)
        self.assertIn("unverified_unknown_action_type", detail)

    def test_click_without_observed_change_is_failure(self):
        with patch("core.eidos_recovery.time.sleep"), \
             patch("core.eidos_recovery._screen_hash", return_value="same"):
            ok, detail = self.verifier.verify_click(10, 20, "same")
        self.assertFalse(ok)
        self.assertEqual(detail, "no_detectable_change")


if __name__ == "__main__":
    unittest.main()
