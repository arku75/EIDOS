from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "tree_triage.py"
SPEC = importlib.util.spec_from_file_location("tree_triage", MODULE_PATH)
tree_triage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tree_triage)


class TreeTriageTests(unittest.TestCase):
    def test_normalized_direct_match_is_not_called_semantic_equivalence(self) -> None:
        comparison = {
            "local_only_project_candidates": [
                "core/eidos_action_verifier.py",
                "core/cortex_grounding.py",
            ],
            "common_all": ["core/action_verifier.py"],
            "tracked_only": ["core/world_engine.py"],
        }
        result = tree_triage.triage(comparison)
        rows = {r["local_path"]: r for r in result["rows"]}
        self.assertEqual(
            rows["core/eidos_action_verifier.py"]["bucket"],
            "direct_normalized_name_match",
        )
        self.assertEqual(
            rows["core/eidos_action_verifier.py"]["best_github_name_match"],
            "core/action_verifier.py",
        )
        self.assertEqual(
            rows["core/cortex_grounding.py"]["bucket"],
            "name_unique_review",
        )
        self.assertIn("not semantic equivalence", result["warning"].lower())

        backup = tree_triage.triage({
            "local_only_project_candidates": ["core/db_backup.py"],
            "common_all": ["core/db.py"],
            "tracked_only": [],
        })["rows"][0]
        self.assertNotEqual(backup["bucket"], "direct_normalized_name_match")

    def test_only_local_core_python_enters_triage(self) -> None:
        result = tree_triage.triage({
            "local_only_project_candidates": [
                "core/eidos_agent.py",
                "bin/eidos_agent.py",
                "core/readme.md",
            ],
            "common_all": ["core/agent.py"],
            "tracked_only": [],
        })
        self.assertEqual(result["local_only_core_python"], 1)


if __name__ == "__main__":
    unittest.main()
