import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class TestServicePortability(unittest.TestCase):
    def test_tracked_service_units_do_not_embed_ser_home(self):
        offenders = []
        for path in sorted((ROOT / "services").glob("*.service")):
            text = path.read_text(encoding="utf-8")
            if "/home/ser/" in text or "/home/ser\n" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [], f"developer-specific service paths: {offenders}")

    def test_learning_daemon_install_is_isolated_and_portable(self):
        installer = (ROOT / "daemon" / "install_daemon.sh").read_text(encoding="utf-8")
        unit = (ROOT / "daemon" / "eidos-learning.service").read_text(encoding="utf-8")

        self.assertNotIn("/home/ser/", installer)
        self.assertNotIn("My_Gpt", installer)
        self.assertNotIn("--break-system-packages", installer)
        self.assertIn('python3', installer)
        self.assertIn('-m venv', installer)
        self.assertIn("daemon/eidos_learning_daemon.py", installer)

        self.assertNotIn("/home/ser/", unit)
        self.assertNotIn("User=ser", unit)
        self.assertIn("EIDOS_VENV/bin/python EIDOS_ROOT/daemon/eidos_learning_daemon.py", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)

    def test_services_bind_to_portable_user_home(self):
        for name in (
            "eidos-websocket.service",
            "eidos-supervisor.service",
            "eidos-identity.service",
            "eidos-pruning.service",
        ):
            text = (ROOT / "services" / name).read_text(encoding="utf-8")
            self.assertIn("WorkingDirectory=%h/EIDOS", text)
            self.assertIn("Environment=PYTHONPATH=%h/EIDOS", text)


if __name__ == "__main__":
    unittest.main()
