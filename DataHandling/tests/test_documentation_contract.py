import ast
from pathlib import Path
import re
import unittest


class DocumentationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend_root = Path(__file__).resolve().parents[1]
        cls.repo_root = cls.backend_root.parent
        cls.server_source = (cls.backend_root / "server.py").read_text(encoding="utf-8")
        cls.server_tree = ast.parse(cls.server_source)
        cls.api_document = (cls.backend_root / "docs" / "PUBLIC_API.md").read_text(
            encoding="utf-8"
        )

    def test_documented_http_routes_match_active_explicit_routes(self):
        active_routes = set()
        for node in ast.walk(self.server_tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id == "app"
                    and decorator.func.attr
                    in {"get", "post", "put", "patch", "delete", "websocket"}
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    continue
                active_routes.add((decorator.func.attr.upper(), decorator.args[0].value))

        expected_routes = {
            ("GET", "/health"),
            ("WEBSOCKET", "/ws/{client_id}"),
            ("GET", "/api/users"),
            ("GET", "/api/users/{user_id}/consent"),
            ("POST", "/api/users/{user_id}/consent"),
            ("GET", "/api/users/{user_id}/forms"),
            ("GET", "/api/forms/{form_id}"),
            ("GET", "/api/forms/{form_id}/progress"),
            ("POST", "/api/forms/{form_id}/attachments"),
        }
        self.assertEqual(active_routes, expected_routes)
        for method, path in expected_routes:
            with self.subTest(method=method, path=path):
                self.assertIn(f"`{path}`", self.api_document)

    def test_documented_websocket_inputs_match_dispatcher(self):
        inbound_types = set()
        for node in ast.walk(self.server_tree):
            if not (
                isinstance(node, ast.Compare)
                and isinstance(node.left, ast.Name)
                and node.left.id == "msg_type"
            ):
                continue
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(
                    comparator.value, str
                ):
                    inbound_types.add(comparator.value)

        expected = {
            "start_interview",
            "start_new_form",
            "load_form",
            "text_input",
            "audio_start",
            "audio_end",
            "end_session",
        }
        self.assertEqual(inbound_types, expected)
        for message_type in expected:
            self.assertIn(f"`{message_type}`", self.api_document)

    def test_entrypoint_docs_do_not_restore_removed_architecture_claims(self):
        combined = "\n".join(
            (
                (self.repo_root / "README.md").read_text(encoding="utf-8"),
                self.api_document,
                (self.backend_root / "docs" / "LOCAL_DEV.md").read_text(
                    encoding="utf-8"
                ),
                (self.backend_root / "docs" / "README.md").read_text(
                    encoding="utf-8"
                ),
                (self.backend_root / "docs" / "DEPLOYMENT.md").read_text(
                    encoding="utf-8"
                ),
            )
        )
        forbidden = (
            "src/orchestrator",
            "deterministic_orchestrator",
            "whisper finishes",
            "whisper model",
            "start_keyword_thread",
            "already checked in",
            "keys (already present)",
        )
        for fragment in forbidden:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, combined.lower())

    def test_environment_template_contains_no_secret_values(self):
        template = self.backend_root / ".env.example"
        values = {}
        for line in template.read_text(encoding="utf-8").splitlines():
            if not line or line.lstrip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key] = value

        sensitive = {
            "GEMINI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "OBSERVABILITY_HASH_KEY",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "MCP_AUTH_TOKEN",
        }
        self.assertTrue(sensitive.issubset(values))
        self.assertEqual({key: values[key] for key in sensitive}, {key: "" for key in sensitive})

    def test_compose_files_have_no_obsolete_version_or_unused_gemini_key_path(self):
        for path in (
            self.repo_root / "docker-compose.yml",
            self.backend_root / "deployment" / "docker-compose.yml",
        ):
            with self.subTest(path=path):
                source = path.read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"^version\s*:", source, re.MULTILINE))
                self.assertNotIn("GEMINI_LLM_SERVICE_CREDENTIALS", source)

    def test_readme_document_links_resolve(self):
        readme = (self.repo_root / "README.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^]]+\]\((DataHandling/docs/[^)]+)\)", readme)
        self.assertGreaterEqual(len(links), 3)
        for link in links:
            self.assertTrue((self.repo_root / link).is_file(), link)


if __name__ == "__main__":
    unittest.main()
