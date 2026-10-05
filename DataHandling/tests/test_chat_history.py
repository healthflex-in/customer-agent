import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.audit.chat_history import (
    TTL_INDEX_NAME,
    VOICE_INPUT_SOURCE,
    build_pending_voice_input,
    build_chat_message_document,
    ensure_chat_history_indexes,
    record_chat_message,
    resolve_patient_input_provenance,
)


class ChatHistoryTests(unittest.TestCase):
    def setUp(self):
        self.created_at = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)

    def test_message_has_investigation_context_and_ten_day_expiry(self):
        user_id = "699c2877476d59d4a3a8cc12"
        document = build_chat_message_document(
            user_id=user_id,
            session_id="session-1",
            form_id="FRM-01",
            attempt_id="attempt-1",
            interview_id="interview-1",
            role="patient",
            content="My knee pain is five out of ten.",
            message_type="text_input",
            phase="interviewing",
            section="Pain Assessment",
            request_id="request-1",
            question_id="severity",
            input_source="voice_transcription",
            transcription_id="TRN-test",
            audio_capture_id="capture-test",
            audio_filename="capture-test.webm",
            retention_days=10,
            created_at=self.created_at,
        )

        self.assertEqual(document["userId"], user_id)
        self.assertEqual(document["sessionId"], "session-1")
        self.assertEqual(document["attemptId"], "attempt-1")
        self.assertEqual(document["role"], "patient")
        self.assertEqual(document["content"], "My knee pain is five out of ten.")
        self.assertEqual(document["inputSource"], "voice_transcription")
        self.assertEqual(document["transcriptionId"], "TRN-test")
        self.assertEqual(document["audioCaptureId"], "capture-test")
        self.assertEqual(document["audioFilename"], "capture-test.webm")
        self.assertEqual(document["createdAt"], self.created_at)
        self.assertEqual(
            document["expiresAt"], self.created_at + timedelta(days=10)
        )

    def test_ttl_and_lookup_indexes_are_created(self):
        collection = Mock()

        ensure_chat_history_indexes(collection)

        collection.create_index.assert_any_call(
            [("expiresAt", 1)],
            name=TTL_INDEX_NAME,
            expireAfterSeconds=0,
        )
        collection.create_index.assert_any_call(
            [("sessionId", 1), ("createdAt", 1)],
            name="chat_history_session_created_at",
        )
        self.assertEqual(collection.create_index.call_count, 4)

    def test_write_failure_is_non_fatal_and_does_not_log_content(self):
        collection = Mock()
        collection.insert_one.side_effect = RuntimeError("sensitive patient text")

        result = record_chat_message(
            collection,
            user_id="699c2877476d59d4a3a8cc12",
            session_id="session-1",
            role="agent",
            content="Sensitive response",
            retention_days=10,
            created_at=self.created_at,
        )

        self.assertFalse(result)

    def test_invalid_or_internal_prompt_content_is_not_stored(self):
        collection = Mock()

        result = record_chat_message(
            collection,
            user_id="699c2877476d59d4a3a8cc12",
            session_id="session-1",
            role="patient",
            content="You are a medical transcription assistant",
            retention_days=10,
            created_at=self.created_at,
        )

        self.assertFalse(result)
        collection.insert_one.assert_not_called()

    def test_unchanged_transcript_is_recognized_for_legacy_clients(self):
        pending = build_pending_voice_input(
            transcript="My knee pain is five out of ten.",
            transcription_id="TRN-test",
            audio_capture_id="capture-test",
            audio_filename="/tmp/debug/capture-test.webm",
        )

        result = resolve_patient_input_provenance(
            content="  my knee pain is five out of ten. ",
            payload={},
            pending_voice_input=pending,
        )

        self.assertEqual(result["inputSource"], VOICE_INPUT_SOURCE)
        self.assertEqual(result["transcriptionId"], "TRN-test")
        self.assertEqual(result["audioCaptureId"], "capture-test")
        self.assertEqual(result["audioFilename"], "capture-test.webm")

    def test_transcription_id_preserves_voice_source_after_patient_edit(self):
        pending = build_pending_voice_input(
            transcript="My knee pain is five.",
            transcription_id="TRN-test",
            audio_capture_id="capture-test",
            audio_filename="capture-test.webm",
        )

        result = resolve_patient_input_provenance(
            content="My right knee pain is six.",
            payload={
                "transcriptionId": "TRN-test",
                # Client-provided file metadata must never override server state.
                "audioFilename": "spoofed.webm",
            },
            pending_voice_input=pending,
        )

        self.assertEqual(result["inputSource"], VOICE_INPUT_SOURCE)
        self.assertEqual(result["audioFilename"], "capture-test.webm")

    def test_legacy_client_keeps_voice_source_when_transcript_is_edited(self):
        pending = build_pending_voice_input(
            transcript="Discarded transcript",
            transcription_id="TRN-test",
        )

        result = resolve_patient_input_provenance(
            content="Patient edited the transcript before sending",
            payload={},
            pending_voice_input=pending,
        )

        self.assertEqual(result["inputSource"], VOICE_INPUT_SOURCE)
        self.assertEqual(result["transcriptionId"], "TRN-test")

    def test_stale_transcription_id_cannot_claim_current_audio_file(self):
        pending = build_pending_voice_input(
            transcript="Current transcript",
            transcription_id="TRN-current",
            audio_capture_id="capture-current",
            audio_filename="capture-current.webm",
        )

        result = resolve_patient_input_provenance(
            content="Stale transcript",
            payload={"transcriptionId": "TRN-old"},
            pending_voice_input=pending,
        )

        self.assertEqual(result, {"inputSource": "typed"})


if __name__ == "__main__":
    unittest.main()
