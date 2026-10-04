import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.env_loader import _sanitize_credentials, ensure_eidos_home
from config.loader import _expand_env_vars, load_config


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
