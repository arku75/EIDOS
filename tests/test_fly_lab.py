import tempfile
import unittest
from pathlib import Path

from core.fly_lab import SparseConnectome, load_connectome, validate_synthetic


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


if __name__ == "__main__":
    unittest.main()
