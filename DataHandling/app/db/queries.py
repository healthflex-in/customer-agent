"""Small, testable helpers for safe MongoDB query construction."""

from __future__ import annotations

from typing import Any, Callable, Optional


def build_user_form_queries(
    user_id: Any,
    form_id: str,
    object_id_factory: Optional[Callable[[Any], Any]] = None,
    attempt_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return identity-compatible queries scoped to one exact form.

    Historical records may store ``userId`` as either an ObjectId or a string,
    so both representations are queried. Every query includes ``formId``;
    user-only fallbacks are deliberately prohibited because form identifiers
    select different questionnaires.
    """

    if user_id is None or str(user_id).strip() == "":
        raise ValueError("User ID is required")
    if not form_id:
        raise ValueError("Form ID is required")

    if object_id_factory is None:
        from bson import ObjectId

        object_id_factory = ObjectId

    user_variants: list[Any] = []
    try:
        user_variants.append(object_id_factory(user_id))
    except Exception:
        pass
    user_variants.append(user_id)

    queries: list[dict[str, Any]] = []
    seen: set[tuple[type, str]] = set()
    for variant in user_variants:
        identity = (type(variant), str(variant))
        if identity in seen:
            continue
        seen.add(identity)
        query = {"userId": variant, "formId": form_id}
        if attempt_id is not None:
            normalized_attempt_id = str(attempt_id).strip()
            if not normalized_attempt_id:
                raise ValueError("Attempt ID cannot be empty")
            query["attemptId"] = normalized_attempt_id
        queries.append(query)
    return queries


def collection_namespace(collection: Any, default_name: str) -> tuple[str, str]:
    """Return collection names without evaluating a PyMongo Collection as bool."""

    if collection is None:
        return "unknown", default_name
    return collection.database.name, collection.name


def empty_tagged_questions_result() -> tuple[list, dict, None]:
    """Return the stable three-part result expected by PROM callers."""

    return [], {}, None
