import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class WebSocketAudioAuthenticationRegressionTests(unittest.TestCase):
    def test_audio_path_does_not_require_retired_access_token_context(self):
        server_source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")

        self.assertNotIn("auth_context", server_source)
        self.assertNotIn("Authentication required", server_source)
        self.assertNotIn("Authentication is required", server_source)


if __name__ == "__main__":
    unittest.main()
