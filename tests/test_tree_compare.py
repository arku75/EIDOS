from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "tree_compare.py"
SPEC = importlib.util.spec_from_file_location("tree_compare", MODULE_PATH)
tree_compare = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tree_compare)


class TreeCompareTests(unittest.TestCase):
    def test_parse_and_classify(self) -> None:
        sample = """[ 20K]  /home/ser/EIDOS/
├── [4.0K]  core/
│   ├── [ 10K]  alive.py
│   └── [ 10K]  alive.py.pre-s1.bak
├── [4.0K]  archivo/
│   └── [ 10K]  old.py
└── [4.0K]  .venv-py314/
    └── [ 10K]  dependency.py
"""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "tree.txt"
            path.write_text(sample, encoding="utf-8")
            entries = tree_compare.parse_tree_snapshot(path)

        files = {x["path"] for x in entries if not x["is_dir"]}
        self.assertEqual(
            files,
            {
                "core/alive.py",
                "core/alive.py.pre-s1.bak",
                "archivo/old.py",
                ".venv-py314/dependency.py",
            },
        )
        self.assertEqual(tree_compare.classify("core/alive.py"), "project_candidate")
        self.assertEqual(
            tree_compare.classify("core/alive.py.pre-s1.bak"),
            "backup_history",
        )
        self.assertEqual(tree_compare.classify("archivo/old.py"), "backup_history")
        self.assertEqual(
            tree_compare.classify(".venv-py314/dependency.py"),
            "generated_env_vcs_cache",
        )

    def test_compare_keeps_history_out_of_missing_code(self) -> None:
        entries = [
            {"path": "core/a.py", "is_dir": False},
            {"path": "core/a.py.pre-s1.bak", "is_dir": False},
            {"path": "archivo/old.py", "is_dir": False},
        ]
        result = tree_compare.compare(entries, {"core/b.py"})
        self.assertEqual(result["local_only_project_candidates"], ["core/a.py"])
        self.assertEqual(result["tracked_only"], ["core/b.py"])


if __name__ == "__main__":
    unittest.main()
