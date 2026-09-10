"""Exact-record ownership guards for database mutations.

Keep ownership-sensitive query construction in a small, testable module so
callers cannot accidentally fall back to broad identifiers such as ``formId``.
"""

from __future__ import annotations

from typing import Any, Mapping

def build_owned_form_filter(
    form: Mapping[str, Any], expected_user_id: Any, expected_form_id: str
) -> dict[str, Any]:
    """Build a MongoDB filter for one form after validating its ownership.

    The returned filter includes the document's ``_id``, stored ``userId`` and
    ``formId``.  Using the stored user ID preserves compatibility with legacy
    records that may contain either strings or ``ObjectId`` values.

    Raises:
        ValueError: if a required identifier is absent or the form ID differs.
        PermissionError: if the document does not belong to the expected user.
    """

    if not form:
        raise ValueError("Form document is required")
    if expected_user_id is None or str(expected_user_id).strip() == "":
        raise ValueError("Expected user ID is required")
    if not expected_form_id:
        raise ValueError("Expected form ID is required")

    document_id = form.get("_id")
    stored_user_id = form.get("userId")
    stored_form_id = form.get("formId")

    if document_id is None or str(document_id).strip() == "":
        raise ValueError("Form document ID is required")
    if stored_user_id is None or str(stored_user_id).strip() == "":
        raise ValueError("Stored user ID is required")
    if str(stored_user_id) != str(expected_user_id):
        raise PermissionError("Form does not belong to the expected user")
    if stored_form_id != expected_form_id:
        raise ValueError("Form ID does not match the expected form")

    # fetch_form_by_id serializes _id for WebSocket/API use. Restore ObjectId
    # when appropriate so this exact-record filter matches MongoDB storage.
    normalized_document_id = document_id
    if isinstance(document_id, str) and _looks_like_object_id(document_id):
        # Imported only when needed so the pure guard remains importable by
        # lightweight tooling that does not install the full MongoDB client.
        from bson import ObjectId

        normalized_document_id = ObjectId(document_id)

    return {
        "_id": normalized_document_id,
        "userId": stored_user_id,
        "formId": stored_form_id,
    }


def _looks_like_object_id(value: str) -> bool:
    """Return whether *value* has MongoDB ObjectId's 24-hex representation."""

    return len(value) == 24 and all(character in "0123456789abcdefABCDEF" for character in value)
