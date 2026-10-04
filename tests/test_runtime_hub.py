import unittest

from core.runtime_hub import EIDOSRuntimeHub


class TestRuntimeHub(unittest.TestCase):
    def setUp(self):
        self.hub = EIDOSRuntimeHub()

    def test_required_architecture_visible(self):
        components = self.hub.components()
        for name in ("world", "actions", "action-verifier", "causal-loop", "characters", "character-neurons", "neural-graph", "fly", "agent-bus"):
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

    def test_action_proposal_has_correlation_id(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 400, "y": 300},
            source="test",
            expected_outcome="results appear",
        )
        self.assertTrue(proposal["proposal_id"].startswith("proposal-"))
        self.assertEqual(proposal["expected_outcome"], "results appear")

    def test_observed_effect_closes_verification_loop(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 400, "y": 300, "reason": "open results"},
            source="test",
            expected_outcome="results appear",
        )
        before = {
            "window_title": "EIDOS Test",
            "ocr_full_text": "Search",
            "regions": [{"text": "Search"}],
        }
        after = {
            "window_title": "EIDOS Test — Results",
            "ocr_full_text": "Search Results Ready",
            "regions": [{"text": "Search"}, {"text": "Results"}, {"text": "Ready"}],
        }

        predictions_before = self.hub.world_model.stats()["total_predictions"]
        result = self.hub.verify_action_effect(proposal, before, after)

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["verification"]["verified"])
        self.assertEqual(result["verification"]["verdict"], "verified")
        self.assertTrue(result["outcome_recorded"])
        self.assertEqual(
            self.hub.world_model.stats()["total_predictions"],
            predictions_before + 1,
        )
        self.assertEqual(
            self.hub.board_read("last_action_outcome")["proposal_id"],
            proposal["proposal_id"],
        )

    def test_no_effect_is_not_reported_as_success(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 400, "y": 300},
            source="test",
        )
        snapshot = {
            "window_title": "EIDOS Test",
            "ocr_full_text": "No change",
            "regions": [{"text": "No change"}],
        }
        result = self.hub.verify_action_effect(proposal, snapshot, snapshot)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verification"]["verified"])
        self.assertEqual(result["verification"]["verdict"], "stuck")

    def test_fly_validation_writes_result_to_shared_state(self):
        result = self.hub.fly_validate(317)
        self.assertTrue(result["passed"])
        self.assertEqual(self.hub.board_read("fly.last_validation")["seed"], 317)


if __name__ == "__main__":
    unittest.main()
