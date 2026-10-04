import tempfile
import unittest
from pathlib import Path

from core.antibiblioteca import Antibiblioteca


class TestAntibibliotecaCausality(unittest.TestCase):
    def make_store(self, root):
        return Antibiblioteca(Path(root) / "negative.db")

    def test_unverified_report_does_not_change_future_choice(self):
        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(td)
            before = store.rank_strategies(
                goal="open app", context="desktop", candidates=[("click", 1.0), ("keyboard", 0.9)]
            )
            store.record_failure(
                goal="open app", context="desktop", strategy="click",
                reason="self says failed", evidence_source="self_report", confidence=1.0,
            )
            after = store.rank_strategies(
                goal="open app", context="desktop", candidates=[("click", 1.0), ("keyboard", 0.9)]
            )
            self.assertEqual(before, after)
            self.assertEqual(store.penalty(goal="open app", context="desktop", strategy="click"), 0.0)

    def test_verified_failure_changes_future_choice(self):
        with tempfile.TemporaryDirectory() as td:
            store = self.make_store(td)
            before = store.rank_strategies(
                goal="open app", context="desktop", candidates=[("click", 1.0), ("keyboard", 0.9)]
            )
            self.assertEqual(before[0][0], "click")
            store.record_failure(
                goal="open app", context="desktop", strategy="click",
                reason="no observable effect", evidence_source="action-verifier", confidence=0.8,
            )
            after = store.rank_strategies(
                goal="open app", context="desktop", candidates=[("click", 1.0), ("keyboard", 0.9)]
            )
            self.assertEqual(after[0][0], "keyboard")

    def test_verified_failure_survives_restart_and_still_changes_choice(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "negative.db"
            first = Antibiblioteca(db)
            first.record_failure(
                goal="search", context="browser", strategy="dom-click",
                reason="target stale", consequence="no navigation",
                evidence_source="runtime-observer", confidence=0.9,
            )
            first.conn.close()

            restarted = Antibiblioteca(db)
            ranked = restarted.rank_strategies(
                goal="search", context="browser",
                candidates=[("dom-click", 1.0), ("keyboard-nav", 0.85)],
            )
            self.assertEqual(ranked[0][0], "keyboard-nav")
            evidence = restarted.evidence(goal="search", context="browser")
            self.assertEqual(len(evidence), 1)
            self.assertTrue(evidence[0]["verified"])


if __name__ == "__main__":
    unittest.main()
