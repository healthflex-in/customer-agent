import ast
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.observability import ai_usage
from app.observability.ai_usage import (
    AIUsage,
    bedrock_usage,
    gemini_usage,
    load_pricing_catalog,
    tracked_ai_call,
)


class AIUsageTests(unittest.TestCase):
    def test_gemini_direct_sdk_usage_separates_audio_modality(self):
        response = type(
            "Response",
            (),
            {
                "usage_metadata": {
                    "promptTokenCount": 140,
                    "candidatesTokenCount": 20,
                    "thoughtsTokenCount": 2,
                    "cachedContentTokenCount": 5,
                    "promptTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 40},
                        {"modality": "AUDIO", "tokenCount": 100},
                    ],
                }
            },
        )()

        self.assertEqual(
            gemini_usage(response),
            AIUsage(
                measured=True,
                input_text_tokens=35,
                input_audio_tokens=100,
                output_tokens=22,
                cached_text_tokens=5,
            ),
        )

    def test_gemini_llamaindex_raw_snake_case_usage_is_supported(self):
        response = type(
            "Response",
            (),
            {
                "raw": {
                    "usage_metadata": {
                        "prompt_token_count": 12,
                        "candidates_token_count": 3,
                    }
                }
            },
        )()

        self.assertEqual(
            gemini_usage(response),
            AIUsage(measured=True, input_text_tokens=12, output_tokens=3),
        )

    def test_bedrock_converse_usage_is_supported(self):
        self.assertEqual(
            bedrock_usage({"usage": {"inputTokens": 70, "outputTokens": 11}}),
            AIUsage(measured=True, input_text_tokens=70, output_tokens=11),
        )

    def test_verified_gemini_rates_are_model_and_modality_specific(self):
        usage = AIUsage(
            input_text_tokens=1_000_000,
            input_audio_tokens=1_000_000,
            output_tokens=1_000_000,
        )

        self.assertAlmostEqual(
            ai_usage.estimate_cost_usd("google_genai", "gemini-2.5-flash", usage),
            3.80,
        )
        self.assertAlmostEqual(
            ai_usage.estimate_cost_usd(
                "google_genai", "gemini-2.5-flash-lite", usage
            ),
            0.80,
        )

    def test_unknown_provider_rate_is_never_guessed(self):
        self.assertIsNone(
            ai_usage.estimate_cost_usd(
                "aws_bedrock",
                "amazon.nova-2-lite-v1:0",
                AIUsage(input_text_tokens=100),
            )
        )

    def test_deployment_pricing_override_is_versioned_and_validated(self):
        raw = json.dumps(
            {
                "aws_bedrock/amazon.nova-2-lite-v1:0": {
                    "input_text_per_million": 0.06,
                    "output_per_million": 0.24,
                },
                "google_speech/v1-latest_short-enhanced": {
                    "audio_per_minute": 0.024,
                    "audio_billing_increment_seconds": 1,
                },
            }
        )
        version, catalog = load_pricing_catalog(
            {"AI_PRICING_VERSION": "billing-contract-7", "AI_PRICING_JSON": raw}
        )

        self.assertEqual(version, "billing-contract-7")
        self.assertEqual(
            catalog["aws_bedrock/amazon.nova-2-lite-v1:0"].output_per_million,
            0.24,
        )
        self.assertEqual(
            catalog["google_speech/v1-latest_short-enhanced"].audio_per_minute,
            0.024,
        )

    def test_audio_pricing_applies_configured_billing_increment(self):
        rate = ai_usage.PriceRate(
            audio_per_minute=0.024,
            audio_billing_increment_seconds=1,
        )
        with patch.dict(
            ai_usage.PRICING_CATALOG,
            {"google_speech/test": rate},
            clear=False,
        ):
            self.assertAlmostEqual(
                ai_usage.estimate_cost_usd(
                    "google_speech", "test", AIUsage(audio_seconds=1.2)
                ),
                0.0008,
            )

    def test_invalid_pricing_fails_at_startup(self):
        cases = (
            {"AI_PRICING_JSON": "[]"},
            {
                "AI_PRICING_VERSION": "test",
                "AI_PRICING_JSON": '{"missing-separator": {}}',
            },
            {
                "AI_PRICING_VERSION": "test",
                "AI_PRICING_JSON": json.dumps(
                    {"provider/model": {"output_per_million": -1}}
                )
            },
            {
                "AI_PRICING_VERSION": "test",
                "AI_PRICING_JSON": json.dumps(
                    {"provider/model": {"unknown_unit": 1}}
                )
            },
            {
                "AI_PRICING_VERSION": "test",
                "AI_PRICING_JSON": json.dumps(
                    {
                        "provider/model": {
                            "audio_billing_increment_seconds": 0
                        }
                    }
                ),
            },
        )
        for environment in cases:
            with self.subTest(environment=environment):
                with self.assertRaises(ValueError):
                    load_pricing_catalog(environment)

    def test_tracked_call_records_success_and_failure_without_content(self):
        sensitive = "patient private transcript"
        response = type("Response", (), {"usage_metadata": {}})()
        with patch.object(ai_usage, "record_ai_call") as record:
            returned = tracked_ai_call(
                provider="google_genai",
                model="gemini-2.5-flash",
                operation="test",
                call=lambda: response,
                usage_extractor=gemini_usage,
            )
        self.assertIs(returned, response)
        self.assertEqual(record.call_args.kwargs["status"], "success")
        self.assertNotIn(sensitive, repr(record.call_args))

        with patch.object(ai_usage, "record_ai_call") as record:
            with self.assertRaisesRegex(RuntimeError, "private transcript"):
                tracked_ai_call(
                    provider="test",
                    model="test",
                    operation="test",
                    call=lambda: (_ for _ in ()).throw(RuntimeError(sensitive)),
                )
        self.assertEqual(record.call_args.kwargs["status"], "error")
        self.assertEqual(record.call_args.kwargs["failure_type"], "RuntimeError")
        self.assertNotIn(sensitive, repr(record.call_args))

    def test_usage_parser_failure_does_not_discard_provider_response(self):
        response = object()
        with patch.object(ai_usage, "record_ai_call") as record:
            returned = tracked_ai_call(
                provider="provider",
                model="model",
                operation="test",
                call=lambda: response,
                usage_extractor=lambda _response: (_ for _ in ()).throw(ValueError()),
            )

        self.assertIs(returned, response)
        self.assertEqual(record.call_args.kwargs["status"], "success")
        self.assertEqual(record.call_args.kwargs["failure_type"], "UsageValueError")

    def test_prometheus_export_contains_bounded_provider_dimensions(self):
        try:
            from prometheus_client import generate_latest
        except ImportError:
            self.skipTest("prometheus_client is optional")

        ai_usage.record_ai_call(
            provider="test_provider",
            model="test_model",
            operation="test_operation",
            status="success",
            elapsed_seconds=0.01,
            usage=AIUsage(input_text_tokens=2, output_tokens=1),
        )
        payload = generate_latest().decode("utf-8")

        self.assertIn("ai_provider_calls_total", payload)
        self.assertIn('provider="test_provider"', payload)
        self.assertIn('model="test_model"', payload)
        self.assertIn('operation="test_operation"', payload)
        self.assertIn("ai_provider_unpriced_calls_total", payload)

    def test_every_active_provider_invocation_crosses_telemetry_boundary(self):
        project_root = Path(__file__).resolve().parents[1]
        paths = [
            project_root / "server.py",
            project_root / "docscanner" / "client.py",
            project_root / "app" / "audio" / "stt.py",
            project_root / "src" / "llm" / "utils.py",
            project_root / "src" / "llm" / "functionalities.py",
            project_root / "src" / "graph" / "nodes" / "extract.py",
        ]
        provider_methods = {"generate_content", "complete", "converse", "recognize"}
        violations = []

        for path in paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            parents = {}
            for parent in ast.walk(tree):
                for child in ast.iter_child_nodes(parent):
                    parents[child] = parent
            for node in ast.walk(tree):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in provider_methods
                ):
                    continue
                ancestor = parents.get(node)
                protected = False
                while ancestor is not None:
                    if (
                        isinstance(ancestor, ast.Call)
                        and isinstance(ancestor.func, ast.Name)
                        and ancestor.func.id == "tracked_ai_call"
                    ):
                        protected = True
                        break
                    ancestor = parents.get(ancestor)
                if not protected:
                    violations.append(f"{path.relative_to(project_root)}:{node.lineno}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
