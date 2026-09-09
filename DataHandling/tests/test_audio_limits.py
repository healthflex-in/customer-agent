from __future__ import annotations

import unittest

from app.audio.limits import audio_limit_error


class AudioLimitTests(unittest.TestCase):
    def test_accepts_audio_within_byte_and_duration_limits(self) -> None:
        self.assertIsNone(
            audio_limit_error(
                current_bytes=100,
                incoming_bytes=50,
                elapsed_seconds=10,
                max_total_bytes=200,
                max_duration_seconds=30,
            )
        )

    def test_rejects_chunk_before_total_buffer_exceeds_limit(self) -> None:
        error = audio_limit_error(
            current_bytes=180,
            incoming_bytes=21,
            elapsed_seconds=10,
            max_total_bytes=200,
            max_duration_seconds=30,
        )

        self.assertIn("too large", error)

    def test_rejects_server_measured_duration(self) -> None:
        error = audio_limit_error(
            current_bytes=100,
            incoming_bytes=0,
            elapsed_seconds=31,
            max_total_bytes=200,
            max_duration_seconds=30,
        )

        self.assertIn("too long", error)

    def test_rejects_client_declared_duration(self) -> None:
        error = audio_limit_error(
            current_bytes=100,
            incoming_bytes=0,
            elapsed_seconds=10,
            max_total_bytes=200,
            max_duration_seconds=30,
            declared_duration_seconds=31,
        )

        self.assertIn("too long", error)

    def test_rejects_invalid_limit_configuration(self) -> None:
        with self.assertRaises(ValueError):
            audio_limit_error(
                current_bytes=0,
                incoming_bytes=0,
                elapsed_seconds=0,
                max_total_bytes=0,
                max_duration_seconds=30,
            )


if __name__ == "__main__":
    unittest.main()
