import ast
import re
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


class LegacyAudioCleanupTests(unittest.TestCase):
    def setUp(self):
        self.server_source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        self.server_tree = ast.parse(self.server_source)

    def test_unreachable_server_audio_helpers_are_absent(self):
        retired = {"save_audio", "text_to_speech", "stream_audio_to_client"}
        definitions = {
            node.name
            for node in self.server_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        called_names = set()
        for node in ast.walk(self.server_tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
        self.assertTrue(retired.isdisjoint(definitions))
        self.assertTrue(retired.isdisjoint(called_names))

    def test_server_does_not_import_retired_helper_dependencies(self):
        imported = set()
        for node in self.server_tree.body:
            if isinstance(node, ast.Import):
                imported.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.update(alias.asname or alias.name for alias in node.names)
        for name in ("np", "wave", "base64", "io", "Path", "AudioSegment"):
            self.assertNotIn(name, imported)

    def test_stt_still_owns_pydub_and_production_dependency(self):
        stt_source = (BACKEND_ROOT / "app" / "audio" / "stt.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("from pydub import AudioSegment", stt_source)
        requirements = (BACKEND_ROOT / "requirements.in").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?m)^pydub==")

    def test_tts_cache_is_not_part_of_runtime_contract(self):
        config = (BACKEND_ROOT / "app" / "config.py").read_text(encoding="utf-8")
        self.assertNotIn("TTS_CACHE_DIR", config)
        deployment_files = (
            BACKEND_ROOT / "deployment" / "Dockerfile",
            BACKEND_ROOT / "deployment" / "docker-compose.yml",
            PROJECT_ROOT / "docker-compose.yml",
        )
        for path in deployment_files:
            self.assertNotIn("tts_cache", path.read_text(encoding="utf-8"))

    def test_commented_duplicate_server_snapshot_is_gone(self):
        self.assertTrue(self.server_source.startswith("from fastapi import ("))
        self.assertNotRegex(
            self.server_source,
            re.compile(r"(?m)^#\s*(?:async\s+)?def\s+websocket_endpoint\("),
        )


if __name__ == "__main__":
    unittest.main()
