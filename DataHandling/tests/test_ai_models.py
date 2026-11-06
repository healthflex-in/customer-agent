from pathlib import Path
import unittest

from app.ai.models import (
    GeminiModelRegistry,
    build_model_registry,
)


class GeminiModelRegistryTests(unittest.TestCase):
    def test_defaults_resolve_to_expected_models(self):
        registry = build_model_registry({})

        self.assertEqual(registry.general, "gemini-3.5-flash-lite")
        self.assertEqual(registry.reasoning, "gemini-3.5-flash-lite")
        self.assertEqual(registry.audio, "gemini-3.5-flash-lite")
        self.assertEqual(registry.search, "gemini-3.5-flash-lite")
        self.assertEqual(
            registry.rotation,
            ("gemini-3.5-flash-lite",),
        )

    def test_all_roles_are_environment_configurable(self):
        registry = build_model_registry({
            "GEMINI_GENERAL_MODEL": "gemini-2.5-flash",
            "GEMINI_REASONING_MODEL": "gemini-3.5-flash-lite",
            "GEMINI_AUDIO_MODEL": "gemini-3.5-flash-lite",
            "GEMINI_SEARCH_MODEL": "gemini-3.5-flash-lite",
            "GEMINI_FALLBACK_MODELS": "gemini-3.5-flash-lite",
        })

        self.assertEqual(registry.general, "gemini-2.5-flash")
        self.assertEqual(registry.reasoning, "gemini-3.5-flash-lite")
        self.assertEqual(registry.audio, "gemini-3.5-flash-lite")
        self.assertEqual(registry.search, "gemini-3.5-flash-lite")

    def test_models_prefix_is_normalized(self):
        registry = build_model_registry({
            "GEMINI_GENERAL_MODEL": "models/gemini-2.5-flash",
        })

        self.assertEqual(registry.general, "gemini-2.5-flash")

    def test_empty_model_name_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            build_model_registry({"GEMINI_REASONING_MODEL": "   "})

    def test_explicit_empty_fallback_list_disables_rotation(self):
        registry = build_model_registry({"GEMINI_FALLBACK_MODELS": ""})

        self.assertEqual(registry.fallbacks, ())
        self.assertEqual(registry.rotation, (registry.general,))

    def test_rotation_removes_duplicates_without_reordering(self):
        registry = GeminiModelRegistry(
            general="gemini-2.5-flash",
            reasoning="gemini-2.5-flash",
            audio="gemini-3.5-flash-lite",
            search="gemini-2.5-flash",
            fallbacks=(
                "gemini-3.5-flash-lite",
                "gemini-2.5-flash",
                "gemini-2.5-flash",
            ),
        )

        self.assertEqual(
            registry.rotation,
            ("gemini-2.5-flash", "gemini-3.5-flash-lite"),
        )

    def test_reasoning_provider_has_a_bounded_request_timeout(self):
        project_root = Path(__file__).resolve().parents[1]
        gemini_source = (project_root / "app" / "ai" / "gemini.py").read_text(encoding="utf-8")
        config_source = (project_root / "app" / "config.py").read_text(encoding="utf-8")

        self.assertIn("GEMINI_REQUEST_TIMEOUT_MS", config_source)
        self.assertIn("http_options=types.HttpOptions(", gemini_source)
        self.assertIn("timeout=timeout_ms", gemini_source)
        self.assertIn("timeout_ms: int = GEMINI_REQUEST_TIMEOUT_MS", gemini_source)


if __name__ == "__main__":
    unittest.main()
