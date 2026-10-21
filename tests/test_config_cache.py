from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dart_insight.config import ConfigurationError, Settings, load_settings
from dart_insight.data.cache import JsonCache


class ConfigCacheTests(unittest.TestCase):
    def test_environment_precedence_and_keys_hidden(self):
        with patch.dict("os.environ", {"DART_API_KEY": "env-secret"}, clear=True):
            with patch("dart_insight.config.dotenv_values", return_value={"DART_API_KEY": "file-secret"}):
                settings = load_settings({"DART_API_KEY": "secret-store", "OPENAI_API_KEY": "openai-secret"})
        self.assertEqual(settings.dart_api_key, "env-secret")
        self.assertEqual(settings.openai_api_key, "openai-secret")
        self.assertNotIn("env-secret", repr(settings))
        self.assertNotIn("openai-secret", repr(settings))

    def test_invalid_settings(self):
        for values in ({"chunk_overlap": 1000}, {"top_k": 0}, {"timeout_seconds": 0},
                       {"request_interval_seconds": -1}, {"list_cache_ttl_seconds": -1}):
            with self.subTest(values=values), self.assertRaises(ConfigurationError):
                Settings(**values)
        with patch.dict("os.environ", {"TOP_K": "invalid"}, clear=True):
            with self.assertRaises(ConfigurationError):
                load_settings()

    def test_cache_expiry_disabled_corruption_and_deterministic_key(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = JsonCache(Path(directory))
            with patch("dart_insight.data.cache.time.time", return_value=100):
                cache.put("test", {"b": 2, "a": 1}, {"ok": True})
            with patch("dart_insight.data.cache.time.time", return_value=105):
                self.assertEqual(cache.get("test", {"a": 1, "b": 2}, 10), {"ok": True})
                self.assertIsNone(cache.get("test", {"a": 1, "b": 2}, 0))
            with patch("dart_insight.data.cache.time.time", return_value=110):
                self.assertIsNone(cache.get("test", {"a": 1, "b": 2}, 10))
            next(Path(directory).rglob("*.json")).write_text("broken")
            self.assertIsNone(cache.get("test", {"a": 1, "b": 2}, 10))
