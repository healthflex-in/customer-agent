import unittest

from app.ai.thinking import bounded_thinking


class BoundedThinkingTests(unittest.TestCase):
    def test_production_2_5_models_keep_a_zero_thinking_budget(self):
        for model_name in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            with self.subTest(model_name=model_name):
                thinking = bounded_thinking(model_name)
                self.assertEqual(thinking.as_kwargs(), {"thinking_budget": 0})

    def test_3_5_flash_lite_uses_minimal_thinking_level(self):
        thinking = bounded_thinking("gemini-3.5-flash-lite")

        self.assertEqual(thinking.as_kwargs(), {"thinking_level": "minimal"})

    def test_unregistered_model_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "No bounded thinking control"):
            bounded_thinking("gemini-3.8-flash")


if __name__ == "__main__":
    unittest.main()
