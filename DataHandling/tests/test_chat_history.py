import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.audit.chat_history import (
    TTL_INDEX_NAME,
    build_chat_message_document,
    ensure_chat_history_indexes,
    record_chat_message,
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
            retention_days=10,
            created_at=self.created_at,
        )

        self.assertEqual(document["userId"], user_id)
        self.assertEqual(document["sessionId"], "session-1")
        self.assertEqual(document["attemptId"], "attempt-1")
        self.assertEqual(document["role"], "patient")
        self.assertEqual(document["content"], "My knee pain is five out of ten.")
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


if __name__ == "__main__":
    unittest.main()
