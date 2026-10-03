import unittest

from core.runtime_hub import EIDOSRuntimeHub


class TestRuntimeHub(unittest.TestCase):
    def setUp(self):
        self.hub = EIDOSRuntimeHub()

    def test_required_architecture_visible(self):
        components = self.hub.components()
        for name in ("world", "actions", "characters", "character-neurons", "neural-graph", "fly", "agent-bus"):
            self.assertIn(name, components)
            self.assertTrue(components[name]["available"], name)

    def test_snapshot_is_non_executing(self):
        snapshot = self.hub.snapshot()
        self.assertFalse(snapshot["safety"]["executes_actions"])
        self.assertFalse(snapshot["safety"]["starts_gui"])
        self.assertFalse(snapshot["safety"]["starts_network_services"])
        self.assertFalse(snapshot["safety"]["fly_writes_live_graph"])

    def test_blackboard_roundtrip(self):
        version = self.hub.board_write("test.runtime", {"ok": True}, agent="test")
        self.assertGreaterEqual(version, 1)
        self.assertEqual(self.hub.board_read("test.runtime"), {"ok": True})

    def test_action_is_proposal_only(self):
        proposal = self.hub.propose_action({"action": "click", "x": 1, "y": 2}, source="test")
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["requires_verification"])
        self.assertEqual(self.hub.board_read("last_action_proposal")["source"], "test")

    def test_fly_validation_writes_result_to_shared_state(self):
        result = self.hub.fly_validate(317)
        self.assertTrue(result["passed"])
        self.assertEqual(self.hub.board_read("fly.last_validation")["seed"], 317)


if __name__ == "__main__":
    unittest.main()
