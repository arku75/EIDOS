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
            }
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

    def test_unknown_agent_is_rejected(self):
        result = self.community.record_verified_outcome(
            "colony_missing",
            True,
            1.0,
            proposal_id="p3",
        )
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
