import unittest
from unittest.mock import patch

from core.usb_hid_backend import USBHIDBackend


class _FailingPath:
    def write_bytes(self, _data):
        raise OSError("synthetic write failure")


class _WorkingPath:
    def write_bytes(self, _data):
        return 8


class TestUSBHIDDispatchContract(unittest.TestCase):
    def test_gold_keyboard_success_is_dispatch_not_world_verification(self):
        b = USBHIDBackend()
        b._tier = "gold"
        b._hidg_kbd = _WorkingPath()
        out = b.keyboard_press(0x04)
        self.assertTrue(out["dispatched"])
        self.assertEqual(out["actual_backend"], "usb_gadget_hid")
        self.assertEqual(out["verification"], "pending")

    def test_gold_keyboard_failure_is_not_silent(self):
        b = USBHIDBackend()
        b._tier = "gold"
        b._hidg_kbd = _FailingPath()
        out = b.keyboard_press(0x04)
        self.assertFalse(out["dispatched"])
        self.assertIn("synthetic write failure", out["error"])
        self.assertEqual(b.stats()["last_dispatch"]["verification"], "pending")

    def test_silver_keyboard_does_not_claim_unimplemented_uinput(self):
        b = USBHIDBackend()
        b._tier = "silver"
        out = b.keyboard_press(0x04)
        self.assertFalse(out["dispatched"])
        self.assertEqual(out["error"], "keyboard_not_implemented")

    @patch("core.usb_hid_backend.subprocess.run")
    def test_bronze_records_real_process_result(self, run):
        run.return_value.returncode = 1
        b = USBHIDBackend()
        b._tier = "bronze"
        out = b.keyboard_press(0x04)
        self.assertFalse(out["dispatched"])
        self.assertEqual(out["actual_backend"], "xdotool")
        self.assertEqual(out["verification"], "pending")


if __name__ == "__main__":
    unittest.main()
