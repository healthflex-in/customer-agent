"""Explicit persisted-form lifecycle and expiration policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping


DRAFT = "draft"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
VALID_STATUSES = {DRAFT, IN_PROGRESS, COMPLETED}
TTL_INDEX_NAME = "ttl_abandoned_forms"
DEFAULT_DRAFT_TTL_DAYS = 7


@dataclass(frozen=True)
class FormLifecycle:
    status: str
    expires_at: datetime | None
    completed_at: datetime | None


def has_meaningful_form_data(value: object) -> bool:
    """Return whether nested form data contains at least one real value."""

    if isinstance(value, Mapping):
        return any(has_meaningful_form_data(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(has_meaningful_form_data(item) for item in value)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def resolve_form_lifecycle(
    form_data: Mapping[str, object],
    *,
    requested_status: str | None = None,
    existing_status: str | None = None,
    now: datetime | None = None,
    existing_completed_at: datetime | None = None,
    draft_ttl_days: int = DEFAULT_DRAFT_TTL_DAYS,
) -> FormLifecycle:
    """Resolve a safe, monotonic lifecycle for a form save.

    Completion is irreversible through ordinary saves. Empty forms are drafts
    with a bounded lifetime; any meaningful answer removes expiration.
    """

    if requested_status is not None and requested_status not in VALID_STATUSES:
        raise ValueError(f"Unsupported form lifecycle status: {requested_status}")
    if draft_ttl_days <= 0:
        raise ValueError("draft_ttl_days must be positive")

    timestamp = now or datetime.now(timezone.utc)
    if existing_status == COMPLETED or requested_status == COMPLETED:
        return FormLifecycle(
            status=COMPLETED,
            expires_at=None,
            completed_at=existing_completed_at or timestamp,
        )

    if has_meaningful_form_data(form_data):
        return FormLifecycle(
            status=IN_PROGRESS,
            expires_at=None,
            completed_at=None,
        )

    return FormLifecycle(
        status=DRAFT,
        expires_at=timestamp + timedelta(days=draft_ttl_days),
        completed_at=None,
    )


def build_lifecycle_update_filter(
    document_id: object,
    *,
    requested_status: str | None,
) -> dict[str, object]:
    """Build the atomic guard used by background form updates.

    Only a caller explicitly reporting the terminal event may write an already
    completed record. This prevents delayed ordinary saves from overwriting the
    terminal snapshot or restoring draft expiration.
    """

    update_filter: dict[str, object] = {"_id": document_id}
    if requested_status != COMPLETED:
        update_filter["status"] = {"$ne": COMPLETED}
    return update_filter


def ensure_form_lifecycle_ttl_index(collection: object) -> None:
    """Replace the legacy title/createdAt TTL with explicit draft expiration."""

    indexes = collection.index_information()
    current = indexes.get(TTL_INDEX_NAME)
    expected_key = [("expiresAt", 1)]
    expected_filter = {"status": DRAFT}
    if current and (
        current.get("key") != expected_key
        or current.get("expireAfterSeconds") != 0
        or current.get("partialFilterExpression") != expected_filter
    ):
        collection.drop_index(TTL_INDEX_NAME)

    collection.create_index(
        expected_key,
        expireAfterSeconds=0,
        partialFilterExpression=expected_filter,
        name=TTL_INDEX_NAME,
    )
