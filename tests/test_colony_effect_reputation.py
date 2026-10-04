import tempfile
import unittest
from pathlib import Path

from core.colony_community import ColonyCommunity


class TestColonyEffectReputation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.community = object.__new__(ColonyCommunity)
        self.community.db_path = Path(self.tmp.name) / "colony-test.db"
        self.community._current_session = "test-session"
        self.community._participants = {
            "colony_coder": {
                "agent_id": "colony_coder",
                "name": "Coder",
                "emoji": "👨‍💻",
            },
            "colony_analyst": {
                "agent_id": "colony_analyst",
                "name": "Analyst",
                "emoji": "🔍",
            },
        }
        self.community._message_handlers = []
        self.community._init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_verified_and_failed_outcomes_change_reputation(self):
        ok = self.community.record_verified_outcome(
            "colony_coder",
            True,
            0.8,
            reason="expected UI transition observed",
            proposal_id="p1",
            action_type="click",
            evidence={"verdict": "verified"},
        )
        bad = self.community.record_verified_outcome(
            "colony_coder",
            False,
            0.5,
            reason="screen unchanged",
            proposal_id="p2",
            action_type="click",
            evidence={"verdict": "stuck"},
        )

        self.assertTrue(ok["success"])
        self.assertTrue(bad["success"])
        self.assertAlmostEqual(ok["reputation_delta"], 0.8)
        self.assertAlmostEqual(bad["reputation_delta"], -0.5)

        stats = self.community.get_agent_outcome_stats("colony_coder")
        self.assertEqual(stats["total_outcomes"], 2)
        self.assertEqual(stats["verified_count"], 1)
        self.assertEqual(stats["failed_count"], 1)
        self.assertAlmostEqual(stats["verification_rate"], 0.5)
        self.assertAlmostEqual(stats["reputation_score"], 0.3)

    def test_repeated_effects_change_future_routing(self):
        initial = ["colony_analyst", "colony_coder"]
        self.assertEqual(
            self.community._rank_responders_by_effect(initial),
            initial,
        )

        for i in range(5):
            self.community.record_verified_outcome(
                "colony_coder",
                True,
                1.0,
                reason="verified success",
                proposal_id=f"coder-{i}",
                action_type="test",
            )
            self.community.record_verified_outcome(
                "colony_analyst",
                False,
                1.0,
                reason="verified failure",
                proposal_id=f"analyst-{i}",
                action_type="test",
            )

        ranked = self.community._rank_responders_by_effect(initial)
        self.assertEqual(ranked[0], "colony_coder")
        self.assertEqual(ranked[1], "colony_analyst")

    def test_single_outcome_does_not_overpower_semantic_order(self):
        initial = ["colony_analyst", "colony_coder"]
        self.community.record_verified_outcome(
            "colony_coder",
            True,
            1.0,
            proposal_id="single",
            action_type="test",
        )
        self.assertEqual(
            self.community._rank_responders_by_effect(initial),
            initial,
        )

    def test_self_report_cannot_change_reputation(self):
        result = self.community.record_verified_outcome(
            "colony_coder", True, 1.0,
            proposal_id="self-claim",
            evidence={"evidence_source": "self"},
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            self.community.get_agent_outcome_stats("colony_coder")["total_outcomes"],
            0,
        )

    def test_inherited_knowledge_cannot_mint_reputation(self):
        result = self.community.record_verified_outcome(
            "colony_coder", True, 1.0,
            proposal_id="inherited",
            evidence={"evidence_source": "inheritance"},
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            self.community.get_agent_outcome_stats("colony_coder")["reputation_score"],
            0.0,
        )

    def test_unknown_agent_is_rejected(self):
        result = self.community.record_verified_outcome(
            "colony_missing",
            True,
            1.0,
            proposal_id="p3",
        )
        self.assertFalse(result["success"])


    def test_effect_learning_survives_restart_and_changes_future_routing(self):
        initial = ["colony_analyst", "colony_coder"]
        for i in range(5):
            self.community.record_verified_outcome(
                "colony_coder", True, 1.0,
                reason="independently verified success",
                proposal_id=f"persist-coder-{i}",
                action_type="test",
            )
            self.community.record_verified_outcome(
                "colony_analyst", False, 1.0,
                reason="independently verified failure",
                proposal_id=f"persist-analyst-{i}",
                action_type="test",
            )

        restarted = object.__new__(ColonyCommunity)
        restarted.db_path = self.community.db_path
        restarted._current_session = "restart-session"
        restarted._participants = dict(self.community._participants)
        restarted._message_handlers = []
        restarted._init_db()

        self.assertEqual(
            restarted._rank_responders_by_effect(initial),
            ["colony_coder", "colony_analyst"],
        )
        stats = restarted.get_agent_outcome_stats("colony_coder")
        self.assertEqual(stats["total_outcomes"], 5)
        self.assertAlmostEqual(stats["reputation_score"], 5.0)

    def test_persistence_without_evidence_does_not_change_routing(self):
        restarted = object.__new__(ColonyCommunity)
        restarted.db_path = self.community.db_path
        restarted._current_session = "empty-restart"
        restarted._participants = dict(self.community._participants)
        restarted._message_handlers = []
        restarted._init_db()
        initial = ["colony_analyst", "colony_coder"]
        self.assertEqual(restarted._rank_responders_by_effect(initial), initial)


if __name__ == "__main__":
    unittest.main()
