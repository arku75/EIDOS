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
