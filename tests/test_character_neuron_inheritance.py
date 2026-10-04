import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from core.character_neuron import CharacterNeuronSystem


SCHEMA = """
CREATE TABLE knowledge_nodes (
    id TEXT PRIMARY KEY,
    concept TEXT,
    quality_score REAL,
    character TEXT
);
"""


class TestCharacterNeuronInheritance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "brain.db"
        with sqlite3.connect(self.db) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO knowledge_nodes VALUES (?,?,?,?)",
                ("py-node", "python asyncio concurrency", 1.0, "colony_python"),
            )
            conn.execute(
                "INSERT INTO knowledge_nodes VALUES (?,?,?,?)",
                ("js-node", "javascript async promise", 1.0, "colony_javascript"),
            )
        self.neurons = CharacterNeuronSystem(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO character_synapses VALUES (?,?,?,?,?)",
                ("colony_python", "py-node", 4.0, 8, time.time()),
            )
            conn.execute(
                "INSERT INTO character_synapses VALUES (?,?,?,?,?)",
                ("colony_javascript", "js-node", 3.0, 6, time.time()),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_child_inherits_parent_synapses_at_reduced_strength(self):
        count = self.neurons.inherit_synapses(
            "colony_pyjs", "colony_python", "colony_javascript"
        )
        self.assertEqual(count, 2)
        with sqlite3.connect(self.db) as conn:
            rows = dict(conn.execute(
                "SELECT node_id, weight FROM character_synapses WHERE character=?",
                ("colony_pyjs",),
            ).fetchall())
        self.assertAlmostEqual(rows["py-node"], 2.0)
        self.assertAlmostEqual(rows["js-node"], 1.5)

    def test_inherited_synapses_change_child_resonance(self):
        before = self.neurons.resonance(
            "colony_pyjs", "python asyncio javascript promise"
        )["score"]
        self.neurons.inherit_synapses(
            "colony_pyjs", "colony_python", "colony_javascript"
        )
        after = self.neurons.resonance(
            "colony_pyjs", "python asyncio javascript promise"
        )["score"]
        self.assertEqual(before, 0.0)
        self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
