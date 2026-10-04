import unittest
from core.self_edit_lab import assess_python_edit


class TestSelfEditLab(unittest.TestCase):
    def test_accepts_small_valid_edit(self):
        original = "def add(a, b):\n    return a + b\n"
        candidate = "def add(a, b):\n    \"\"\"Add two values.\"\"\"\n    return a + b\n"
        result = assess_python_edit(original, candidate)
        self.assertTrue(result.accepted, result.reasons)
        self.assertGreater(result.changed_lines, 0)

    def test_rejects_syntax_error(self):
        result = assess_python_edit("x = 1\n", "x = (\n")
        self.assertFalse(result.accepted)

    def test_rejects_dynamic_exec(self):
        result = assess_python_edit("x = 1\n", "x = 1\neval('2+2')\n")
        self.assertFalse(result.accepted)
        self.assertTrue(any("high-risk" in reason for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
