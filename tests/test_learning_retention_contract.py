import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestLearningRetentionContract(unittest.TestCase):
    def test_cleanup_mark_as_learned_requires_explicit_delete_opt_in(self):
        tree = ast.parse((ROOT / "core" / "cleanup_system.py").read_text(encoding="utf-8"))
        fn = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "mark_as_learned"
        )
        defaults = dict(
            zip(
                [a.arg for a in fn.args.args[-len(fn.args.defaults):]],
                fn.args.defaults,
            )
        )
        self.assertIn("can_delete", defaults)
        self.assertIsInstance(defaults["can_delete"], ast.Constant)
        self.assertIs(defaults["can_delete"].value, False)

    def test_learning_ingestion_never_authorizes_source_deletion(self):
        tree = ast.parse((ROOT / "core" / "learning_system.py").read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "mark_as_learned"
        ]
        self.assertGreaterEqual(len(calls), 1)
        for call in calls:
            kw = {item.arg: item.value for item in call.keywords if item.arg}
            self.assertIn("can_delete", kw)
            self.assertIsInstance(kw["can_delete"], ast.Constant)
            self.assertIs(kw["can_delete"].value, False)


if __name__ == "__main__":
    unittest.main()
