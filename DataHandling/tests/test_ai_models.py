import ast
from pathlib import Path
import unittest

from app.ai.models import (
    GeminiModelRegistry,
    RETIRED_GEMINI_MODELS,
    build_model_registry,
)


class GeminiModelRegistryTests(unittest.TestCase):
    def test_defaults_use_only_approved_stable_models(self):
        registry = build_model_registry({})

        self.assertEqual(registry.general, "gemini-2.5-flash-lite")
        self.assertEqual(registry.reasoning, "gemini-2.5-flash")
        self.assertEqual(registry.audio, "gemini-2.5-flash")
        self.assertEqual(registry.search, "gemini-2.5-flash")
        self.assertEqual(
            registry.rotation,
            ("gemini-2.5-flash-lite", "gemini-2.5-flash"),
        )

    def test_all_roles_are_environment_configurable(self):
        registry = build_model_registry({
            "GEMINI_GENERAL_MODEL": "gemini-2.5-flash",
            "GEMINI_REASONING_MODEL": "gemini-2.5-flash-lite",
            "GEMINI_AUDIO_MODEL": "gemini-2.5-flash-lite",
            "GEMINI_SEARCH_MODEL": "gemini-2.5-flash-lite",
            "GEMINI_FALLBACK_MODELS": "gemini-2.5-flash-lite",
        })

        self.assertEqual(registry.general, "gemini-2.5-flash")
        self.assertEqual(registry.reasoning, "gemini-2.5-flash-lite")
        self.assertEqual(registry.audio, "gemini-2.5-flash-lite")
        self.assertEqual(registry.search, "gemini-2.5-flash-lite")

    def test_models_prefix_is_normalized(self):
        registry = build_model_registry({
            "GEMINI_GENERAL_MODEL": "models/gemini-2.5-flash-lite",
        })

        self.assertEqual(registry.general, "gemini-2.5-flash-lite")

    def test_retired_model_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "retired Gemini model"):
            build_model_registry({
                "GEMINI_FALLBACK_MODELS": "gemini-1.5-flash",
            })

    def test_unknown_or_preview_model_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "unapproved Gemini model"):
            build_model_registry({
                "GEMINI_REASONING_MODEL": "gemini-future-preview",
            })

    def test_explicit_empty_fallback_list_disables_rotation(self):
        registry = build_model_registry({"GEMINI_FALLBACK_MODELS": ""})

        self.assertEqual(registry.fallbacks, ())
        self.assertEqual(registry.rotation, (registry.general,))

    def test_rotation_removes_duplicates_without_reordering(self):
        registry = GeminiModelRegistry(
            general="gemini-2.5-flash-lite",
            reasoning="gemini-2.5-flash",
            audio="gemini-2.5-flash",
            search="gemini-2.5-flash",
            fallbacks=(
                "gemini-2.5-flash-lite",
                "gemini-2.5-flash",
                "gemini-2.5-flash",
            ),
        )

        self.assertEqual(
            registry.rotation,
            ("gemini-2.5-flash-lite", "gemini-2.5-flash"),
        )

    def test_active_python_does_not_reference_retired_model_literals(self):
        project_root = Path(__file__).resolve().parents[1]
        registry_file = project_root / "app" / "ai" / "models.py"
        offenders = []

        for source_path in project_root.rglob("*.py"):
            if source_path == registry_file or "tests" in source_path.parts:
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value in RETIRED_GEMINI_MODELS
                ):
                    offenders.append(
                        f"{source_path.relative_to(project_root)}:{node.lineno}"
                    )

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
