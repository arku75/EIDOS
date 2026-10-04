import tempfile
import unittest
from pathlib import Path

from core.fly_lab import (
    SparseConnectome,
    load_connectome,
    validate_connectome_signal,
    validate_synthetic,
)


class TestFlyLab(unittest.TestCase):
    def test_synthetic_positive_beats_shuffled_control(self):
        result = validate_synthetic(317)
        self.assertGreaterEqual(result.baseline_accuracy, 0.80)
        self.assertGreaterEqual(result.margin, 0.35)
        self.assertTrue(result.passed)

    def test_sparse_connectome_propagates(self):
        graph = SparseConnectome()
        graph.add_edge(1, 2, 2.0)
        out = graph.propagate({1: 1.0}, decay=0.5)
        self.assertAlmostEqual(out[2], 1.0)

    def test_csv_loader(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "edges.csv"
            path.write_text("source,target,weight\n1,2,3\n2,3,4\n", encoding="utf-8")
            graph = load_connectome(path)
            self.assertEqual(graph.edge_count, 2)
            self.assertEqual(graph.node_count, 3)
            self.assertEqual(graph.provenance["format"], "csv")
            self.assertFalse(graph.provenance["live_graph_mutated"])

    def test_connectome_signal_beats_shuffled_wiring_control(self):
        graph = SparseConnectome({"dataset": "fixture"})
        graph.add_edge(1, 10, 4.0)
        graph.add_edge(1, 11, 3.0)
        graph.add_edge(2, 12, 2.0)
        graph.add_edge(3, 13, 1.0)
        result = validate_connectome_signal(
            graph,
            {1: 1.0, 2: 0.25},
            seed=317,
        )
        self.assertTrue(result["distinct_from_shuffled"])
        self.assertGreater(result["l1_distance_from_shuffled"], 0.0)
        self.assertFalse(result["live_graph_mutated"])
        self.assertEqual(result["provenance"]["dataset"], "fixture")


if __name__ == "__main__":
    unittest.main()
