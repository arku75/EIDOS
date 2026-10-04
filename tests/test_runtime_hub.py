import unittest
from unittest.mock import patch

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
        result = self.hub.verify_action_effect(
            proposal, before, after, evidence_source="runtime-observer"
        )

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
        result = self.hub.verify_action_effect(
            proposal, snapshot, snapshot, evidence_source="runtime-observer"
        )
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["verification"]["verified"])
        self.assertEqual(result["verification"]["verdict"], "stuck")

    def test_colony_source_gets_effect_reputation(self):
        class FakeCommunity:
            def __init__(self):
                self.calls = []

            def record_verified_outcome(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {
                    "success": True,
                    "agent_id": args[0],
                    "verified": args[1],
                    "confidence": args[2],
                }

        fake = FakeCommunity()
        proposal = self.hub.propose_action(
            {"action": "click", "x": 400, "y": 300},
            source="colony_coder",
            expected_outcome="results appear",
        )
        before = {
            "window_title": "EIDOS Test",
            "ocr_full_text": "Search",
            "regions": [{"text": "Search"}],
        }
        after = {
            "window_title": "EIDOS Test — Results",
            "ocr_full_text": "Search Results",
            "regions": [{"text": "Search"}, {"text": "Results"}],
        }

        with patch(
            "core.colony_community.get_colony_community",
            return_value=fake,
        ):
            result = self.hub.verify_action_effect(
                proposal,
                before,
                after,
                evidence_source="runtime-observer",
            )

        self.assertTrue(result["colony_reputation"]["success"])
        self.assertEqual(result["colony_reputation"]["agent_id"], "colony_coder")
        self.assertEqual(len(fake.calls), 1)

    def test_actor_cannot_credit_its_own_evidence(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 400, "y": 300},
            source="colony_coder",
            expected_outcome="results appear",
        )
        before = {
            "window_title": "Before",
            "ocr_full_text": "Search",
            "regions": [{"text": "Search"}],
        }
        after = {
            "window_title": "After",
            "ocr_full_text": "Search Results",
            "regions": [{"text": "Search"}, {"text": "Results"}],
        }

        with patch("core.colony_community.get_colony_community") as mocked:
            result = self.hub.verify_action_effect(
                proposal,
                before,
                after,
                evidence_source="colony_coder",
            )

        self.assertEqual(result["status"], "untrusted_evidence")
        self.assertFalse(result["evidence_independent"])
        self.assertFalse(result["credited_verified"])
        self.assertIsNone(result["colony_reputation"])
        mocked.assert_not_called()

    def test_evidence_source_is_required(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 1, "y": 2},
            source="colony_coder",
        )
        snapshot = {
            "window_title": "Same",
            "ocr_full_text": "Same",
            "regions": [],
        }
        with self.assertRaises(ValueError):
            self.hub.verify_action_effect(
                proposal,
                snapshot,
                snapshot,
                evidence_source="",
            )

    def test_registered_observations_verify_correlated_proposal(self):
        before = self.hub.record_observation(
            {
                "window_title": "Before",
                "ocr_full_text": "Search",
                "regions": [{"text": "Search"}],
            },
            observer="runtime-observer",
        )
        proposal = self.hub.propose_action(
            {"action": "click", "x": 10, "y": 20},
            source="colony_coder",
            expected_outcome="result appears",
        )
        after = self.hub.record_observation(
            {
                "window_title": "Before",
                "ocr_full_text": "Search",
                "regions": [{"text": "Search"}],
            },
            observer="runtime-observer",
        )
        after = self.hub.record_observation(
            {
                "window_title": "After",
                "ocr_full_text": "Search Results",
                "regions": [{"text": "Search"}, {"text": "Results"}],
            },
            observer="runtime-observer",
        )

        with patch("core.colony_community.get_colony_community") as mocked:
            mocked.return_value.record_verified_outcome.return_value = {
                "success": True,
                "agent_id": "colony_coder",
            }
            result = self.hub.verify_observations(
                proposal,
                before["observation_id"],
                after["observation_id"],
            )

        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["evidence_independent"])
        self.assertTrue(result["credited_verified"])
        self.assertEqual(
            self.hub.action_proposal(proposal["proposal_id"])["proposal_id"],
            proposal["proposal_id"],
        )
        self.assertEqual(
            self.hub.action_outcome(proposal["proposal_id"])["proposal_id"],
            proposal["proposal_id"],
        )

    def test_registered_observations_require_same_observer(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 1, "y": 2},
            source="colony_coder",
        )
        before = self.hub.record_observation(
            {"window_title": "Before", "ocr_full_text": "A", "regions": []},
            observer="observer-a",
        )
        after = self.hub.record_observation(
            {"window_title": "After", "ocr_full_text": "B", "regions": []},
            observer="observer-b",
        )
        with self.assertRaises(ValueError):
            self.hub.verify_observations(
                proposal,
                before["observation_id"],
                after["observation_id"],
            )


    def test_observations_must_bracket_proposal(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 1, "y": 2},
            source="colony_coder",
        )
        before = self.hub.record_observation(
            {"window_title": "Late before", "ocr_full_text": "A", "regions": []},
            observer="runtime-observer",
        )
        after = self.hub.record_observation(
            {"window_title": "After", "ocr_full_text": "B", "regions": []},
            observer="runtime-observer",
        )
        with self.assertRaises(ValueError):
            self.hub.verify_observations(
                proposal, before["observation_id"], after["observation_id"]
            )

    def test_actor_evidence_can_update_world_truth_but_not_reputation(self):
        proposal = self.hub.propose_action(
            {"action": "click", "x": 2, "y": 3},
            source="colony_coder",
            expected_outcome="changed",
        )
        before = {"window_title": "Before", "ocr_full_text": "A", "regions": []}
        after = {
            "window_title": "After",
            "ocr_full_text": "A changed",
            "regions": [{"text": "changed"}],
        }
        with patch.object(self.hub.world_model, "record_outcome") as record, \
             patch("core.colony_community.get_colony_community") as community:
            result = self.hub.verify_action_effect(
                proposal, before, after, evidence_source="colony_coder"
            )
        self.assertTrue(result["observed_verified"])
        self.assertFalse(result["credited_verified"])
        self.assertEqual(result["status"], "untrusted_evidence")
        self.assertTrue(record.call_args.args[1])
        community.assert_not_called()

    def test_observation_requires_provenance(self):
        with self.assertRaises(ValueError):
            self.hub.record_observation(
                {"window_title": "x", "ocr_full_text": "x", "regions": []},
                observer="",
            )

    def test_fly_validation_writes_result_to_shared_state(self):
        result = self.hub.fly_validate(317)
        self.assertTrue(result["passed"])
        self.assertEqual(self.hub.board_read("fly.last_validation")["seed"], 317)


if __name__ == "__main__":
    unittest.main()
