import os
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from app.observability.privacy import env_flag, error_type, pseudonymous_id
from app.mcp_client import is_available as mcp_is_available


class ObservabilityPrivacyTests(unittest.TestCase):
    def test_identifier_is_omitted_without_a_secret_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(pseudonymous_id("patient-123"))

    def test_identifier_is_keyed_deterministic_and_does_not_contain_source(self):
        first = pseudonymous_id("patient-123", key="review-secret")
        second = pseudonymous_id("patient-123", key="review-secret")

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("subject-"))
        self.assertNotIn("patient-123", first)
        self.assertNotEqual(first, pseudonymous_id("patient-123", key="other-secret"))

    def test_empty_identifier_is_never_emitted(self):
        self.assertIsNone(pseudonymous_id("", key="review-secret"))

    def test_error_logging_exposes_only_exception_class(self):
        error = ValueError("patient said a sensitive sentence")

        self.assertEqual(error_type(error), "ValueError")
        self.assertNotIn("sensitive", error_type(error))

    def test_environment_flags_are_explicit_and_default_off(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(env_flag("LANGFUSE_CAPTURE_CONTENT"))
        with patch.dict(os.environ, {"LANGFUSE_CAPTURE_CONTENT": "yes"}, clear=True):
            self.assertTrue(env_flag("LANGFUSE_CAPTURE_CONTENT"))

    def test_mcp_phi_transfer_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"MCP_URL": "http://localhost:9000"}, clear=True):
            self.assertFalse(mcp_is_available())
        with patch.dict(
            os.environ,
            {"MCP_URL": "http://localhost:9000", "MCP_ALLOW_PHI": "true"},
            clear=True,
        ):
            self.assertTrue(mcp_is_available())

    def test_active_log_calls_do_not_restore_known_content_previews(self):
        project_root = Path(__file__).resolve().parents[1]
        paths = [
            project_root / "server.py",
            project_root / "app" / "mcp_client.py",
            *sorted((project_root / "src").rglob("*.py")),
        ]
        forbidden = (
            "Received text input:",
            "LLM FORMATTING RESPONSE",
            "Problematic JSON string",
            "response_preview",
            "transcription[:",
            "next_message[:",
            "case_description[:",
            "_manswer!r",
            "error_details",
        )

        violations = []
        for path in paths:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                is_print = isinstance(node.func, ast.Name) and node.func.id == "print"
                is_log = isinstance(node.func, ast.Attribute) and node.func.attr in {
                    "info",
                    "warning",
                    "error",
                }
                if not (is_print or is_log):
                    continue
                call_source = ast.get_source_segment(source, node) or ""
                for fragment in forbidden:
                    if fragment in call_source:
                        violations.append(f"{path.name}:{node.lineno}:{fragment}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
