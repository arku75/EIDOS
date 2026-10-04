import unittest

from core.character_lifecycle import ABSORPTION_THRESHOLD, CharacterLifecycleManager


class TestCharacterLifecycleContract(unittest.TestCase):
    def _manager(self, p1, p2):
        manager = object.__new__(CharacterLifecycleManager)
        chars = {"colony_python": p1, "colony_javascript": p2}
        manager.get_character = chars.get
        return manager

    def test_mastery_threshold_is_complete(self):
        self.assertEqual(ABSORPTION_THRESHOLD, 1.0)

    def test_incomplete_parent_cannot_create_child(self):
        parent = {"absorption_pct": 1.0}
        incomplete = {"absorption_pct": 0.99}
        manager = self._manager(parent, incomplete)
        self.assertIsNone(
            manager.execute_reproduction("colony_python", "colony_javascript")
        )

    def test_both_incomplete_parents_cannot_create_child(self):
        manager = self._manager(
            {"absorption_pct": 0.90},
            {"absorption_pct": 0.90},
        )
        self.assertIsNone(
            manager.execute_reproduction("colony_python", "colony_javascript")
        )


if __name__ == "__main__":
    unittest.main()
