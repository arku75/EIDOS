import unittest
from core.operation_modes import OperationModeManager


class TestOperationModesSecurity(unittest.TestCase):
    def test_default_is_plan_fail_closed(self):
        m = OperationModeManager()
        self.assertEqual(m.current_mode, "PLAN")
        self.assertFalse(m.permissions.can_write_real)
        self.assertFalse(m.permissions.can_execute_write)
        self.assertFalse(m.permissions.can_use_kali_tools)

    def test_plan_edit_does_not_encode_external_guard_bypass(self):
        m = OperationModeManager("PLAN+EDIT")
        self.assertTrue(m.permissions.can_execute_write)
        source = __import__("inspect").getsource(OperationModeManager)
        self.assertNotIn("bypass", source.lower())


if __name__ == "__main__":
    unittest.main()
