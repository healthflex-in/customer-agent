"""Short-lived customer-agent conversation audit records.

The transcript contains sensitive patient content. Records therefore carry an
explicit expiry timestamp and are removed by MongoDB's TTL monitor. Audit
writes are deliberately best-effort: an observability failure must never stop
or delay the clinical intake flow.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pymongo.collection import Collection

from app.content_safety import scrub_internal_transcription_prompts
from app.observability.privacy import error_type


TTL_INDEX_NAME = "ttl_customer_agent_chat_history"


def ensure_chat_history_indexes(collection: "Collection") -> None:
    """Create expiry and investigation indexes for the audit collection."""

    collection.create_index(
        [("expiresAt", 1)],
        name=TTL_INDEX_NAME,
        expireAfterSeconds=0,
    )
    collection.create_index(
        [("userId", 1), ("createdAt", 1)],
        name="chat_history_user_created_at",
    )
    collection.create_index(
        [("sessionId", 1), ("createdAt", 1)],
        name="chat_history_session_created_at",
    )
    collection.create_index(
        [("attemptId", 1), ("createdAt", 1)],
        name="chat_history_attempt_created_at",
        sparse=True,
    )


def build_chat_message_document(
    *,
    user_id: object,
    session_id: object,
    role: str,
    content: str,
    retention_days: int,
    form_id: object = None,
    attempt_id: object = None,
    interview_id: object = None,
    message_type: str = "text_message",
    phase: object = None,
    section: object = None,
    request_id: object = None,
    question_id: object = None,
    error_code: object = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a bounded, queryable transcript record with deterministic expiry."""

    if role not in {"patient", "agent", "system"}:
        raise ValueError("role must be patient, agent, or system")
    if retention_days <= 0:
        raise ValueError("retention_days must be positive")
    if not str(user_id or "").strip():
        raise ValueError("user_id is required")
    if not str(session_id or "").strip():
        raise ValueError("session_id is required")

    clean_content, _ = scrub_internal_transcription_prompts(str(content or ""))
    clean_content = str(clean_content).strip()
    if not clean_content:
        raise ValueError("content cannot be empty")

    timestamp = created_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    document: dict[str, Any] = {
        # Keep the application identifier as a string. It is stable across
        # legacy ObjectId/string user schemas and is never returned publicly.
        "userId": str(user_id).strip(),
        "sessionId": str(session_id or "").strip(),
        "role": role,
        "content": clean_content,
        "messageType": str(message_type or "text_message"),
        "createdAt": timestamp,
        "expiresAt": timestamp + timedelta(days=retention_days),
    }
    optional_values = {
        "formId": form_id,
        "attemptId": attempt_id,
        "interviewId": interview_id,
        "phase": phase,
        "section": section,
        "requestId": request_id,
        "questionId": question_id,
        "errorCode": error_code,
    }
    for key, value in optional_values.items():
        if value is not None and str(value).strip():
            document[key] = str(value).strip()
    return document


def record_chat_message(
    collection: "Collection | None",
    **message: Any,
) -> bool:
    """Best-effort insert; return False rather than breaking the patient flow."""

    if collection is None:
        return False
    try:
        collection.insert_one(build_chat_message_document(**message))
        return True
    except Exception as exc:
        # Never print the exception payload because it may contain patient text.
        print(f"[chat-history] Audit write failed: {error_type(exc)}")
        return False
