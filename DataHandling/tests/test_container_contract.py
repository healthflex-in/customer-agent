import re
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


def _requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)", line)
        if match:
            names.add(match.group(1).lower().replace("_", "-"))
    return names


class DependencyContractTests(unittest.TestCase):
    def test_direct_requirements_exclude_confirmed_legacy_packages(self):
        direct = _requirement_names(BACKEND_ROOT / "requirements.in")
        obsolete = {
            "edge-tts",
            "google-generativeai",
            "gradio",
            "gtts",
            "llama-index",
            "llama-index-llms-gemini",
            "openai-whisper",
            "pydantic-settings",
            "torch",
            "transformers",
        }
        self.assertTrue(obsolete.isdisjoint(direct), obsolete & direct)

    def test_every_direct_requirement_is_present_in_lock(self):
        direct = _requirement_names(BACKEND_ROOT / "requirements.in")
        locked = _requirement_names(BACKEND_ROOT / "requirements-docker.txt")
        self.assertFalse(direct - locked, direct - locked)

    def test_transitive_lock_uses_exact_versions(self):
        for raw_line in (BACKEND_ROOT / "requirements-docker.txt").read_text(
            encoding="utf-8"
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("#", "--")):
                continue
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^;\s]+(?:\s*;.+)?$")

    def test_local_install_consumes_the_same_lock(self):
        active_lines = [
            line.strip()
            for line in (BACKEND_ROOT / "requirements.txt")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(active_lines, ["-r requirements-docker.txt"])


class ContainerContractTests(unittest.TestCase):
    def setUp(self):
        self.dockerfile = (
            BACKEND_ROOT / "deployment" / "Dockerfile"
        ).read_text(encoding="utf-8")

    def test_image_is_multi_stage_and_build_tools_are_not_in_runtime(self):
        self.assertIn("FROM python:3.12-slim AS builder", self.dockerfile)
        self.assertIn("FROM python:3.12-slim AS runtime", self.dockerfile)
        runtime = self.dockerfile.split("FROM python:3.12-slim AS runtime", 1)[1]
        for package in ("build-essential", "git", "curl"):
            self.assertNotIn(package, runtime)

    def test_image_copies_only_runtime_source_allow_list(self):
        self.assertNotIn("COPY DataHandling/ .", self.dockerfile)
        for source in ("app", "data", "docscanner", "src", "upload"):
            self.assertIn(f"COPY DataHandling/{source} ./{source}", self.dockerfile)
        self.assertIn("COPY DataHandling/server.py ./server.py", self.dockerfile)

    def test_healthcheck_does_not_require_curl(self):
        self.assertIn("urllib.request.urlopen", self.dockerfile)

    def test_build_context_excludes_secrets(self):
        ignore = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
        for rule in ("**/.env", "**/.env.*", "**/config/", "**/*.pem", "**/*.key"):
            self.assertIn(rule, ignore)

    def test_compose_does_not_override_application_with_host_source(self):
        for path in (
            PROJECT_ROOT / "docker-compose.yml",
            PROJECT_ROOT / "docker-compose.dev-isolated.yml",
            BACKEND_ROOT / "deployment" / "docker-compose.yml",
        ):
            compose = path.read_text(encoding="utf-8")
            self.assertNotRegex(compose, r"\./DataHandling:/app(?::|\s)")

    def test_chroma_is_embedded_and_has_no_network_client(self):
        runtime_source = "\n".join(
            path.read_text(encoding="utf-8")
            for root in (BACKEND_ROOT / "app", BACKEND_ROOT / "src")
            for path in root.rglob("*.py")
        )
        runtime_source += (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        self.assertIn("chromadb.PersistentClient(", runtime_source)
        self.assertNotIn("chromadb.HttpClient(", runtime_source)
        self.assertNotIn("chromadb.AsyncHttpClient(", runtime_source)
        for compose_path in (
            PROJECT_ROOT / "docker-compose.yml",
            PROJECT_ROOT / "docker-compose.dev-isolated.yml",
            BACKEND_ROOT / "deployment" / "docker-compose.yml",
        ):
            self.assertNotIn("chroma/chroma", compose_path.read_text(encoding="utf-8"))

    def test_isolated_dev_compose_has_dedicated_resources(self):
        compose = (PROJECT_ROOT / "docker-compose.dev-isolated.yml").read_text(
            encoding="utf-8"
        )
        self.assertTrue(compose.startswith('version: "3.8"'))
        self.assertIn("container_name: customer-agent-dev-isolated", compose)
        self.assertIn("image: stance-customer-agent-dev-isolated:local", compose)
        self.assertIn('"0.0.0.0:8004:8000"', compose)
        self.assertIn("./DataHandling/.env.dev-isolated", compose)
        self.assertIn("./DataHandling/config/dev-isolated:/app/config:ro", compose)
        self.assertIn("name: customer-agent-dev-isolated-network", compose)
        for suffix in (
            "audio",
            "transcripts",
            "output",
            "received-audio",
            "db",
        ):
            self.assertIn(f"name: customer-agent-dev-isolated-{suffix}", compose)


if __name__ == "__main__":
    unittest.main()
