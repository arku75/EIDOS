import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.env_loader import _sanitize_credentials, ensure_eidos_home, load_eidos_dotenv
from config.loader import _expand_env_vars, get_config_path, load_config


class TestConfigLoader(unittest.TestCase):
    def test_shell_style_default_is_supported_without_shell(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                _expand_env_vars("${OLLAMA_URL:-http://localhost:11434}"),
                "http://localhost:11434",
            )

    def test_environment_value_wins_over_default(self):
        with patch.dict(os.environ, {"OLLAMA_URL": "http://127.0.0.1:9999"}, clear=True):
            self.assertEqual(
                _expand_env_vars("${OLLAMA_URL:-http://localhost:11434}"),
                "http://127.0.0.1:9999",
            )

    def test_partial_nested_config_preserves_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            path.write_text("gateway:\n  port: 19000\n", encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config["gateway"]["port"], 19000)
        self.assertEqual(config["gateway"]["host"], "127.0.0.1")
        self.assertEqual(config["gateway"]["enabled_platforms"], ["local"])

    def test_credentials_are_never_silently_rewritten(self):
        value = "token-ñ"
        with patch.dict(os.environ, {"SERVICE_TOKEN": value}, clear=True):
            _sanitize_credentials()
            self.assertEqual(os.environ["SERVICE_TOKEN"], value)

    def test_existing_config_without_yaml_fails_instead_of_using_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            path.write_text("gateway:\n  port: 19000\n", encoding="utf-8")
            with patch("config.loader.HAS_YAML", False):
                with self.assertRaisesRegex(ImportError, "PyYAML"):
                    load_config(path)
                self.assertEqual(load_config(Path(td) / "absent.yaml")["gateway"]["port"], 18789)

    def test_config_path_respects_state_root_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "state"
            override = Path(td) / "explicit.yaml"
            with patch.dict(os.environ, {"EIDOS_HOME": str(home)}, clear=True):
                self.assertEqual(get_config_path(), home / "config.yaml")
                with patch.dict(os.environ, {"EIDOS_CONFIG": str(override)}):
                    self.assertEqual(get_config_path(), override)
            with patch.dict(os.environ, {"HOME": td}, clear=True):
                self.assertEqual(get_config_path(), Path(td) / ".eidos/config.yaml")

    def test_dotenv_stays_in_selected_home_and_explicit_path_wins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            selected = root / "selected"
            explicit = root / "explicit"
            for directory, value in ((root / ".eidos", "wrong-home"),
                                     (selected, "selected"), (explicit, "explicit")):
                directory.mkdir()
                (directory / ".env").write_text(f"EIDOS_CONFIG_TEST={value}\n")
            with patch.dict(os.environ, {"HOME": td, "EIDOS_HOME": str(selected)}, clear=True):
                self.assertEqual(load_eidos_dotenv(), [selected / ".env"])
                self.assertEqual(os.environ["EIDOS_CONFIG_TEST"], "selected")
                self.assertEqual(load_eidos_dotenv(eidos_home=explicit), [explicit / ".env"])
                self.assertEqual(os.environ["EIDOS_CONFIG_TEST"], "explicit")

    def test_config_cli_round_trip_does_not_touch_default_home(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            selected = root / "selected"
            env = {**os.environ, "HOME": td, "EIDOS_HOME": str(selected)}
            env.pop("EIDOS_CONFIG", None)
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "cli/config_cmd.py")]
            for arguments in (["init"], ["set", "gateway.port", "19002"]):
                result = subprocess.run(command + arguments, cwd=td, env=env,
                                        capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run(command + ["get", "gateway.port"], cwd=td, env=env,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "19002")
            self.assertTrue((selected / "config.yaml").exists())
            self.assertFalse((root / ".eidos").exists())

    def test_eidos_home_is_private(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "eidos-home"
            with patch.dict(os.environ, {"EIDOS_HOME": str(home)}, clear=False):
                result = ensure_eidos_home()
            self.assertEqual(result, home)
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((home / "sessions").stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
