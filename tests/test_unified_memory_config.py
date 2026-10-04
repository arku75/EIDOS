import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestUnifiedMemoryConfig(unittest.TestCase):
    def test_import_honors_isolated_home_port_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            home = pathlib.Path(td) / "state"
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
            env["EIDOS_HOME"] = str(home)
            env["EIDOS_CHROMA_PORT"] = "9876"
            code = (
                "import json,stat;"
                "import core.eidos_memory_unified as m;"
                "print(json.dumps({'home':str(m.EIDOS_DIR),'db':str(m.UNIFIED_DB),"
                "'port':m.CHROMA_PORT,'mode':oct(stat.S_IMODE(m.EIDOS_DIR.stat().st_mode))}))"
            )
            proc = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
                timeout=20,
            )
            data = json.loads(proc.stdout.strip().splitlines()[-1])
            self.assertEqual(data["home"], str(home))
            self.assertEqual(data["db"], str(home / "unified_memory.db"))
            self.assertEqual(data["port"], 9876)
            self.assertEqual(data["mode"], "0o700")


if __name__ == "__main__":
    unittest.main()
