"""Form-attempt identity and MongoDB index migration helpers.

``formId`` identifies the questionnaire/template (for example ``FRM-01``).
``attemptId`` identifies one patient's immutable intake attempt using that
template.  Keeping these concepts separate preserves existing deep links while
allowing repeat consultations without overwriting earlier submissions.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, Mapping


ATTEMPT_INDEX_NAME = "unique_user_form_attempt"
ATTEMPT_INDEX_KEYS = [("userId", 1), ("formId", 1), ("attemptId", 1)]
LEGACY_INDEX_KEYS = [("userId", 1), ("formId", 1)]


def new_form_attempt_id() -> str:
    """Return a non-semantic, globally unique identifier for one intake."""

    return f"ATT-{uuid.uuid4()}"


def resolve_form_attempt_id(
    existing_form: Mapping[str, Any] | None,
    *,
    force_new: bool = False,
    id_factory: Callable[[], str] = new_form_attempt_id,
) -> str | None:
    """Select the attempt to resume or allocate for a new intake.

    A legacy stored form deliberately resolves to ``None`` because adding a
    generated ID during resume would insert a copy instead of updating that
    record. Explicit new-intake requests always allocate a different identity.
    """

    if force_new or existing_form is None:
        return id_factory()
    return existing_form.get("attemptId")


def add_attempt_scope(query: dict[str, Any], attempt_id: str | None) -> dict[str, Any]:
    """Add an exact attempt constraint without changing legacy query behavior."""

    scoped = dict(query)
    if attempt_id is not None:
        normalized = str(attempt_id).strip()
        if not normalized:
            raise ValueError("Attempt ID cannot be empty")
        scoped["attemptId"] = normalized
    return scoped


def add_attempt_write_scope(
    query: dict[str, Any], attempt_id: str | None
) -> dict[str, Any]:
    """Scope a write to one attempt, including legacy records explicitly.

    Reads without an attempt may intentionally select the latest record. Writes
    cannot use that ambiguity: an omitted ID is restricted to a pre-migration
    document on which ``attemptId`` does not exist.
    """

    if attempt_id is None:
        return {**query, "attemptId": {"$exists": False}}
    return add_attempt_scope(query, attempt_id)


def ensure_form_attempt_index(collection: Any) -> None:
    """Replace the one-form-per-template constraint with attempt uniqueness.

    Historical documents have no ``attemptId``. MongoDB indexes those as a
    single null value per ``(userId, formId)``, preserving the legacy record.
    New attempts always carry a generated ID and can therefore coexist.
    """

    indexes = collection.index_information()
    expected_keys = list(ATTEMPT_INDEX_KEYS)

    for name, definition in indexes.items():
        keys = list(definition.get("key", []))
        if name == ATTEMPT_INDEX_NAME and keys != expected_keys:
            raise RuntimeError(
                f"Existing {ATTEMPT_INDEX_NAME} index has unexpected keys: {keys}"
            )
        if definition.get("unique") and keys == LEGACY_INDEX_KEYS:
            collection.drop_index(name)

    collection.create_index(
        expected_keys,
        unique=True,
        name=ATTEMPT_INDEX_NAME,
    )
