"""
Pure (stateless) helpers for converting MongoDB documents and primitives
into API-friendly shapes.

These functions do not touch the database. They take a doc / value and
return a value. Keeping them isolated from connection state lets them be
unit-tested without spinning up Mongo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId


def normalize_user_id(user_id: Optional[str]) -> Any:
    """
    Normalize a user_id to a MongoDB ObjectId where possible.

    - If `user_id` parses as a 24-char hex ObjectId, return ObjectId(user_id).
    - Otherwise, return the original value (e.g., for legacy string IDs).
    - If `user_id` is falsy, return None.
    """
    if not user_id:
        return None
    try:
        return ObjectId(user_id)
    except Exception:
        return user_id


def serialize_datetime(value: Any) -> str:
    """
    Stringify a datetime for JSON responses.

    - `None` → ""
    - `datetime` → ISO 8601
    - anything else → str(value), or "" on failure
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return ""


def serialize_user(doc: Optional[dict]) -> Optional[dict]:
    """
    Transform a MongoDB user document into the API-facing shape used by
    `/api/users`.

    Returns None for an empty/None input. Tolerates multiple historical
    schemas: top-level fields, `profileData.*`, `personalDetails.*`, etc.
    """
    if not doc:
        return None

    first = (
        doc.get("firstName")
        or doc.get("first_name")
        or doc.get("profileData", {}).get("firstName")
        or doc.get("personalDetails", {}).get("firstName")
        or ""
    )
    last = (
        doc.get("lastName")
        or doc.get("last_name")
        or doc.get("profileData", {}).get("lastName")
        or doc.get("personalDetails", {}).get("lastName")
        or ""
    )
    full = " ".join(part for part in [first, last] if part).strip()
    if not full:
        full = (
            doc.get("fullName")
            or doc.get("name")
            or doc.get("profileData", {}).get("fullName")
            or doc.get("profileData", {}).get("name")
            or doc.get("personalDetails", {}).get("fullName")
            or doc.get("personalDetails", {}).get("name")
            or doc.get("email")
            or ""
        )

    return {
        "id": str(doc.get("_id")),
        "firstName": first,
        "lastName": last,
        "fullName": full,
    }
