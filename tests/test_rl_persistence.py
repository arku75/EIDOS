import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestRLPersistence(unittest.TestCase):
    def run_code(self, home, code):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["EIDOS_HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT, env=env, text=True, capture_output=True,
            check=True, timeout=20,
        ).stdout.strip().splitlines()[-1]

    def test_single_learning_event_survives_process_restart(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "state"
            first = self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "a.learn('s','good',5.0,'next');"
                "print(a.q_value_report('s')['good'])",
            )
            self.assertGreater(float(first), 0.0)

            second = self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "print(a.q_value_report('s')['good'])",
            )
            self.assertAlmostEqual(float(second), float(first), places=8)

    def test_persisted_learning_transfers_to_similar_unseen_state(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "state"
            self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "a.learn('seen','open-settings',8.0,'next',node_names='gear,settings,window');"
                "print('ok')",
            )
            choice = self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "print(a.best_action('unseen',['open-settings','other'],"
                "node_names='gear,settings,panel'))",
            )
            self.assertEqual(choice, "open-settings")

    def test_persisted_learning_changes_fresh_process_best_action(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "state"
            self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "a.learn('s','learned',8.0,'next');"
                "print('ok')",
            )
            choice = self.run_code(
                home,
                "from core.eidos_rl import QLearningAgent;"
                "a=QLearningAgent();"
                "print(a.best_action('s',['learned','other']))",
            )
            self.assertEqual(choice, "learned")


if __name__ == "__main__":
    unittest.main()
