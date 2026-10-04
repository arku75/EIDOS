from pathlib import Path
import inspect
import unittest

from core import eidos_staging
from core.eidos_self_improvement import SelfImprovementSystem
from core.self_edit_lab import SAFE_REGRESSION_MODULES


ROOT = Path(__file__).resolve().parents[1]


class TestSelfEditGuardrails(unittest.TestCase):
    def test_staging_path_stays_inside_repo(self):
        rel = eidos_staging._repo_relative_path("core/self_edit_lab.py")
        self.assertEqual(rel, Path("core/self_edit_lab.py"))

        absolute = (ROOT / "core" / "self_edit_lab.py").resolve()
        self.assertEqual(
            eidos_staging._repo_relative_path(absolute),
            Path("core/self_edit_lab.py"),
        )

    def test_staging_rejects_path_escape(self):
        outside = (ROOT.parent / "outside.py").resolve()
        with self.assertRaises(ValueError):
            eidos_staging._repo_relative_path(outside)

        with self.assertRaises(ValueError):
            eidos_staging._repo_relative_path("../../outside.py")

    def test_self_improvement_defaults_to_staging(self):
        sig = inspect.signature(SelfImprovementSystem.apply_improvement)
        self.assertIs(sig.parameters["use_staging"].default, True)

    def test_no_silent_direct_fallback(self):
        source = inspect.getsource(SelfImprovementSystem.apply_improvement)
        self.assertNotIn("Fallback a aplicación directa", source)
        self.assertIn("EIDOS_UNSAFE_DIRECT_SELF_EDIT", source)

    def test_removed_nonexistent_test_suite_reference(self):
        staging_source = (ROOT / "core" / "eidos_staging.py").read_text(encoding="utf-8")
        improvement_source = (ROOT / "core" / "eidos_self_improvement.py").read_text(encoding="utf-8")
        self.assertNotIn("tests/test_eidos_suite.py", staging_source)
        self.assertNotIn("tests/test_eidos_suite.py", improvement_source)

    def test_safe_regression_modules_exist(self):
        for module in SAFE_REGRESSION_MODULES:
            path = ROOT / (module.replace(".", "/") + ".py")
            self.assertTrue(path.exists(), f"missing regression module: {module}")


if __name__ == "__main__":
    unittest.main()
