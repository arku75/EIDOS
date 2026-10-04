import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestHebbianPruningContract(unittest.TestCase):
    def run_code(self, home, code):
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        env["EIDOS_HOME"] = str(home)
        return subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, env=env,
            text=True, capture_output=True, check=True, timeout=20,
        ).stdout.strip().splitlines()[-1]

    def test_dry_run_does_not_mutate_weak_edge(self):
        with tempfile.TemporaryDirectory() as td:
            code = """
import json
from core.eidos_hebbian_pruning import HebbianPruner, BRAIN_DB
from core.db import get_conn
p=HebbianPruner(); c=get_conn(BRAIN_DB)
c.execute("INSERT OR REPLACE INTO knowledge_nodes(id,concept) VALUES('a','a'),('b','b')")
c.execute("INSERT OR REPLACE INTO knowledge_edges(from_node,to_node,relation_type,strength) VALUES('a','b','test',0.01)")
c.commit()
r=p.prune(dry_run=True)
left=c.execute("SELECT COUNT(*) FROM knowledge_edges WHERE from_node='a' AND to_node='b'").fetchone()[0]
print(json.dumps({"reported":r["edges_pruned"],"left":left}))
"""
            import json
            out = json.loads(self.run_code(pathlib.Path(td) / "state", code))
            self.assertGreaterEqual(out["reported"], 1)
            self.assertEqual(out["left"], 1)

    def test_real_prune_reports_and_removes_orphan_nodes(self):
        with tempfile.TemporaryDirectory() as td:
            code = """
import json
from core.eidos_hebbian_pruning import HebbianPruner, BRAIN_DB
from core.db import get_conn
p=HebbianPruner(); c=get_conn(BRAIN_DB)
c.execute("INSERT OR REPLACE INTO knowledge_nodes(id,concept,source) VALUES('orphan','orphan','test')")
c.commit()
r=p.prune(dry_run=False)
left=c.execute("SELECT COUNT(*) FROM knowledge_nodes WHERE id='orphan'").fetchone()[0]
print(json.dumps({"nodes_pruned":r["nodes_pruned"],"left":left}))
"""
            import json
            out = json.loads(self.run_code(pathlib.Path(td) / "state", code))
            self.assertGreaterEqual(out["nodes_pruned"], 1)
            self.assertEqual(out["left"], 0)


if __name__ == "__main__":
    unittest.main()
