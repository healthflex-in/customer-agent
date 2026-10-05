import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from app.audio.debug_capture import (
    prune_debug_captures,
    save_debug_audio,
    update_debug_capture,
)


class AudioDebugCaptureTests(unittest.TestCase):
    def test_disabled_capture_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = save_debug_audio(
                b"RIFF" + b"x" * 32,
                enabled=False,
                directory=tmp,
                retention_hours=24,
                max_total_bytes=1024,
            )
            self.assertIsNone(result)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_original_webm_and_paired_transcripts_are_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = b"\x1aE\xdf\xa3" + b"audio" * 20
            capture = save_debug_audio(
                raw,
                enabled=True,
                directory=tmp,
                retention_hours=24,
                max_total_bytes=1024 * 1024,
                metadata={"attemptId": "ATT-test", "declaredDurationSeconds": 2.5},
            )
            self.assertIsNotNone(capture)
            assert capture is not None
            self.assertEqual(capture.audio_path.suffix, ".webm")
            self.assertEqual(capture.audio_path.read_bytes(), raw)

            update_debug_capture(
                capture,
                status="delivered",
                raw_transcript="raw words",
                cleaned_transcript="Raw words",
            )

            self.assertEqual(capture.transcript_path.read_text(), "raw words")
            self.assertEqual(capture.cleaned_transcript_path.read_text(), "Raw words")
            metadata = json.loads(capture.metadata_path.read_text())
            self.assertEqual(metadata["mimeType"], "audio/webm")
            self.assertEqual(metadata["attemptId"], "ATT-test")
            self.assertEqual(metadata["transcriptionStatus"], "delivered")
            self.assertEqual(metadata["rawTranscriptChars"], 9)
            self.assertEqual(metadata["cleanedTranscriptChars"], 9)
            self.assertEqual(capture.audio_path.stat().st_mode & 0o777, 0o600)

    def test_expired_capture_group_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = save_debug_audio(
                b"RIFF" + b"x" * 32,
                enabled=True,
                directory=tmp,
                retention_hours=24,
                max_total_bytes=1024 * 1024,
            )
            assert capture is not None
            update_debug_capture(
                capture,
                status="transcribed",
                raw_transcript="test",
            )
            old = time.time() - (48 * 3600)
            for path in Path(tmp).iterdir():
                os.utime(path, (old, old))

            prune_debug_captures(
                tmp,
                retention_hours=24,
                max_total_bytes=1024 * 1024,
            )

            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
