import unittest
from pathlib import Path
from unittest.mock import patch

from app.audio import stt
from app.content_safety import (
    contains_internal_transcription_prompt,
    scrub_internal_transcription_prompts,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
LEAKED_TRANSCRIPT = (
    "From one. You are a medical transcription assistant. Transcribe EXACTLY "
    "what the patient says word for word. Return ONLY the transcription text, "
    "nothing else."
)


class TranscriptionContentSafetyTests(unittest.TestCase):
    def test_known_internal_prompt_echo_is_detected(self):
        self.assertTrue(contains_internal_transcription_prompt(LEAKED_TRANSCRIPT))
        self.assertTrue(
            contains_internal_transcription_prompt(
                "TRANSCRIBE exactly what the patient says — word for word."
            )
        )

    def test_normal_patient_language_is_not_blocked(self):
        normal_responses = (
            "From a friend.",
            "I work as a medical transcription assistant.",
            "Please transcribe this referral note.",
        )
        for response in normal_responses:
            with self.subTest(response=response):
                self.assertFalse(contains_internal_transcription_prompt(response))

    def test_contaminated_nested_form_values_are_blank_before_persistence(self):
        form = {
            "Referral": {"Source": LEAKED_TRANSCRIPT},
            "Present Complaint": {"Primary Complaint": "Knee pain"},
            "Items": ["safe", LEAKED_TRANSCRIPT],
        }

        cleaned, removed = scrub_internal_transcription_prompts(form)

        self.assertEqual(removed, 2)
        self.assertEqual(cleaned["Referral"]["Source"], "")
        self.assertEqual(cleaned["Present Complaint"]["Primary Complaint"], "Knee pain")
        self.assertEqual(cleaned["Items"], ["safe", ""])
        self.assertEqual(form["Referral"]["Source"], LEAKED_TRANSCRIPT)

    @patch.object(stt, "_pad_with_silence", side_effect=lambda audio: audio)
    @patch.object(stt, "_transcribe_with_gcloud", return_value="From a friend.")
    @patch.object(stt, "_transcribe_with_gemini", return_value=LEAKED_TRANSCRIPT)
    def test_gemini_prompt_echo_is_retried_with_independent_stt(
        self, gemini_mock, gcloud_mock, _pad_mock
    ):
        result = stt.transcribe_audio_bytes(b"audio")

        self.assertEqual(result, "From a friend.")
        gemini_mock.assert_called_once()
        gcloud_mock.assert_called_once()

    @patch.object(stt, "_pad_with_silence", side_effect=lambda audio: audio)
    @patch.object(stt, "_transcribe_with_gcloud", return_value=LEAKED_TRANSCRIPT)
    @patch.object(stt, "_transcribe_with_gemini", return_value=LEAKED_TRANSCRIPT)
    def test_contaminated_fallback_is_rejected(
        self, _gemini_mock, _gcloud_mock, _pad_mock
    ):
        with self.assertRaisesRegex(ValueError, "internal-content guard"):
            stt.transcribe_audio_bytes(b"audio")

    def test_server_has_delivery_submission_persistence_and_read_guards(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")

        self.assertGreaterEqual(source.count("contains_internal_transcription_prompt"), 3)
        self.assertGreaterEqual(source.count("scrub_internal_transcription_prompts"), 5)
        self.assertIn("internal_prompt_content_removed_before_persistence", source)
        self.assertIn("internal_prompt_content_removed_during_form_read", source)
        self.assertIn("internal_prompt_content_removed_during_latest_form_read", source)

    def test_transcription_instruction_is_not_sent_as_user_content(self):
        source = (BACKEND_ROOT / "app" / "audio" / "stt.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("system_instruction=_GEMINI_STT_PROMPT", source)
        self.assertNotIn("_GEMINI_STT_PROMPT,\n            ]", source)


if __name__ == "__main__":
    unittest.main()
