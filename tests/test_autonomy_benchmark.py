import unittest
from core.autonomy_benchmark import run_benchmark


class TestAutonomyBenchmark(unittest.TestCase):
    def test_learning_improves_verified_world_performance(self):
        result = run_benchmark(seed=317, episodes=800)
        self.assertTrue(result.passed, result.to_dict())
        self.assertGreater(result.trained_success, result.untrained_success)


if __name__ == "__main__":
    unittest.main()
