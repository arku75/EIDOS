import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.skill_evolver as skill_evolver


class TestSkillEvolverSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(skill_evolver, "SKILLS_DIR", root / "skills"),
            patch.object(skill_evolver, "FAILURES_LOG", root / "failures.jsonl"),
            patch.object(skill_evolver, "EVOLVED_LOG", root / "evolved.jsonl"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.evolver = skill_evolver.SkillEvolver()

    def tearDown(self):
        self.tmp.cleanup()

    def _evolve(self, error):
        self.evolver.record_failure("exampletool", error, "test")
        self.evolver.record_failure("exampletool", error, "test")
        return self.evolver.evolve()[0]

    def test_permission_failure_never_evolves_sudo_execution(self):
        skill = self._evolve("permission denied")
        self.assertNotIn("sudo ", skill.solution)
        self.assertIn("approval", skill.solution.lower())

    def test_missing_tool_never_evolves_auto_install(self):
        skill = self._evolve("command not found")
        self.assertNotIn("apt install", skill.solution)
        self.assertIn("proposal", skill.solution.lower())


if __name__ == "__main__":
    unittest.main()
