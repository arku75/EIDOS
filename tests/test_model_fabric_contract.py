from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import core.eidos_llm as llm


class ModelFabricContractTests(unittest.TestCase):
    def test_configured_order_only_references_registered_providers(self) -> None:
        registered = {provider.name for provider in llm.PROVIDERS}
        self.assertTrue(llm.ORDER)
        self.assertTrue(set(llm.ORDER).issubset(registered), (llm.ORDER, registered))
        if "ollama" in llm.ORDER:
            self.assertEqual(llm.ORDER[-1], "ollama")

    def test_fallback_moves_to_next_provider_without_network(self) -> None:
        calls: list[str] = []

        def fake_call(provider, system, prompt, max_tokens, fewshot=None):
            calls.append(provider.name)
            if provider.name == "groq":
                return None
            if provider.name == "openrouter":
                return "provider-two-answer"
            self.fail(f"unexpected provider call: {provider.name}")

        with patch.object(llm, "ORDER", ["groq", "openrouter", "ollama"]), \
             patch.object(llm, "_call_provider", side_effect=fake_call):
            result = llm.complete(
                "route this request",
                system="test",
                inject_identity=False,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(result.text, "provider-two-answer")
        self.assertEqual(calls, ["groq", "openrouter"])

    def test_user_facing_completion_does_not_autolearn_by_default(self) -> None:
        old = os.environ.pop("EIDOS_AUTOLEARN", None)
        try:
            with patch.object(llm, "ORDER", ["groq"]), \
                 patch.object(llm, "_call_provider", return_value="safe answer"), \
                 patch.object(llm, "_enforce_identity", side_effect=lambda text, prompt: text), \
                 patch("threading.Thread") as thread_cls:
                result = llm.complete("explain a safe deterministic test")
            self.assertTrue(result.ok)
            thread_cls.assert_not_called()
        finally:
            if old is not None:
                os.environ["EIDOS_AUTOLEARN"] = old

    def test_secrets_follow_eidos_home(self) -> None:
        old_home = os.environ.get("EIDOS_HOME")
        old_key = os.environ.pop("EIDOS_TEST_ONLY_API_KEY", None)
        try:
            with tempfile.TemporaryDirectory(prefix="eidos-llm-home-") as td:
                home = Path(td)
                (home / "secrets.env").write_text(
                    "EIDOS_TEST_ONLY_API_KEY=file-secret\n",
                    encoding="utf-8",
                )
                os.environ["EIDOS_HOME"] = td
                loaded = llm._load_secrets()
                self.assertEqual(
                    loaded.get("EIDOS_TEST_ONLY_API_KEY"),
                    "file-secret",
                )
        finally:
            if old_home is None:
                os.environ.pop("EIDOS_HOME", None)
            else:
                os.environ["EIDOS_HOME"] = old_home
            if old_key is not None:
                os.environ["EIDOS_TEST_ONLY_API_KEY"] = old_key


if __name__ == "__main__":
    unittest.main()
