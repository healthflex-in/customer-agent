"""
Form CRUD functions for the customer-info MongoDB collection.

Forms are unique by the combination of (formId + userId). The formId is fixed
(DEFAULT_FORM_ID) for all users; uniqueness comes from userId. MongoDB enforces
this with a unique compound index created in app.db.mongo.init_mongo().
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import List, Optional

from app.config import DEFAULT_FORM_ID
from app.db.mongo import get_customer_info_collection
from app.db.serializers import normalize_user_id, serialize_datetime
from app.forms.progress import calculate_form_progress


# ── Internal helpers ──────────────────────────────────────────────────────────

def _generate_form_title(form_data: dict) -> str:
    """Derive a human-readable title from the primary complaint."""
    primary_complaint = (
        form_data.get("Present Complaint", {}).get("Primary Complaint", "")
    )
    if not primary_complaint or not primary_complaint.strip():
        return "New Form"
    # Capitalise first letter, truncate if very long
    title = primary_complaint.strip()
    if len(title) > 80:
        title = title[:77] + "..."
    return title.capitalize()


# ── Public CRUD functions ─────────────────────────────────────────────────────

def save_customer_info(
    user_id: str,
    form_data: dict,
    current_section: str,
    form_id: str = None,
    attachments: Optional[List[dict]] = None,
    chat_history: Optional[list] = None,
) -> Optional[str]:
    """
    Save or update customer interview information in MongoDB.

    Forms are unique by the combination of (formId + userId).
    The formId is fixed (same for all users); uniqueness comes from userId.

    Args:
        user_id: The user ID from the selected user (required for uniqueness).
        form_data: The form data dictionary.
        current_section: Current section of the interview.
        form_id: Optional form ID. If None, uses DEFAULT_FORM_ID.
        attachments: Optional list of attachment dicts. If None, existing
            attachments in the DB are preserved.
        chat_history: Ignored — kept for call-site compatibility.

    Returns:
        The form_id of the saved form, or None on failure.
    """
    collection = get_customer_info_collection()
    if collection is None:
        print("Warning: customer-info collection not available. Skipping MongoDB save.")
        return None

    if not user_id:
        print("Error: user_id is required for saving form data")
        return None

    try:
        normalized_user_id = normalize_user_id(user_id)

        if not form_id:
            form_id = DEFAULT_FORM_ID
            print(f"[save_customer_info] Using default form_id: {form_id} for user: {user_id}")
        else:
            print(f"[save_customer_info] Using provided form_id: {form_id} for user: {user_id}")

        # Deep copy to prevent shared-reference bugs across concurrent sessions
        form_data_copy = copy.deepcopy(form_data)

        # Guard: never overwrite a filled form with an empty one.
        # A form is "empty" if every field value is blank.
        def _form_has_data(fd: dict) -> bool:
            return any(
                v for sec in fd.values() if isinstance(sec, dict)
                for v in sec.values() if v and str(v).strip()
            )

        if not _form_has_data(form_data_copy):
            existing_doc = collection.find_one(
                {"formId": form_id, "userId": normalized_user_id},
                {"form_data": 1},
            )
            if existing_doc and _form_has_data(existing_doc.get("form_data", {})):
                print(f"[save_customer_info] BLOCKED — prevented overwriting filled form with empty data for user {user_id}")
                return form_id  # preserve existing filled data

        # Derive title
        form_title = _generate_form_title(form_data_copy)

        # If caller didn't supply attachments, preserve whatever is in the DB
        if attachments is None:
            existing = collection.find_one(
                {"formId": form_id, "userId": normalized_user_id},
                {"attachments": 1},
            )
            attachments_to_store = existing.get("attachments", []) if existing else []
        else:
            attachments_to_store = attachments

        now = datetime.now()
        customer_doc = {
            "userId": normalized_user_id,
            "formId": form_id,
            "title": form_title,
            "timestamp": now.isoformat(),
            "form_data": form_data_copy,
            "current_section": current_section,
            "updatedAt": now,
            "attachments": attachments_to_store,
        }

        # Atomic upsert — no duplicate documents possible.
        # $setOnInsert sets createdAt only when a new document is inserted.
        collection.update_one(
            {"formId": form_id, "userId": normalized_user_id},
            {
                "$set": customer_doc,
                "$setOnInsert": {"createdAt": now},
            },
            upsert=True,
        )
        print(f"Saved customer info for user {user_id}, form {form_id} in MongoDB")
        return form_id

    except Exception as e:
        print(f"Error saving customer info to MongoDB: {e}")
        return None


def fetch_user_forms(user_id: str) -> list:
    """
    Retrieve all forms for a specific user from MongoDB.

    Args:
        user_id: The user ID to retrieve forms for.

    Returns:
        List of form summary dicts with formId, title, timestamp, etc.
    """
    collection = get_customer_info_collection()
    if collection is None:
        print("Warning: customer-info collection not available.")
        return []

    try:
        normalized_user_id = normalize_user_id(user_id)
        forms = list(
            collection.find(
                {"userId": normalized_user_id, "formId": DEFAULT_FORM_ID},
                {"_id": 0},
            ).sort("createdAt", -1)
        )

        return [
            {
                "formId": form.get("formId", ""),
                "title": form.get("title", "Untitled Form"),
                "timestamp": serialize_datetime(form.get("timestamp")),
                "createdAt": serialize_datetime(form.get("createdAt")),
                "updatedAt": serialize_datetime(form.get("updatedAt")),
                "current_section": form.get("current_section", ""),
                "progress": calculate_form_progress(form.get("form_data", {})),
            }
            for form in forms
        ]

    except Exception as e:
        print(f"Error retrieving user forms: {e}")
        return []


def fetch_latest_form_for_user(user_id: str) -> Optional[dict]:
    """
    Retrieve the form for a specific user.

    Since formId is fixed, each user has exactly one form (identified by
    formId + userId). Returns None if the collection is unavailable or no
    document exists.
    """
    collection = get_customer_info_collection()
    if collection is None:
        print("Warning: customer-info collection not available when fetching latest form.")
        return None

    try:
        normalized_user_id = normalize_user_id(user_id)
        form = collection.find_one(
            {"userId": normalized_user_id, "formId": DEFAULT_FORM_ID},
            {"_id": 0},
            sort=[("createdAt", -1)],
        )
        if form:
            form["timestamp"] = serialize_datetime(form.get("timestamp"))
            form["createdAt"] = serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = serialize_datetime(form.get("updatedAt"))
        return form

    except Exception as e:
        print(f"Error retrieving form for user {user_id}: {e}")
        return None


def fetch_form_by_id(form_id: str, user_id: str = None) -> Optional[dict]:
    """
    Retrieve a specific form by form_id and user_id.

    Forms are unique by (formId + userId), so user_id is required.

    Args:
        form_id: The form ID to retrieve.
        user_id: The user ID (required for uniqueness).

    Returns:
        Form document dict, or None if not found.
    """
    collection = get_customer_info_collection()
    if collection is None:
        print("Warning: customer-info collection not available.")
        return None

    if not user_id:
        print("Warning: user_id is required to fetch form by form_id")
        return None

    try:
        normalized_user_id = normalize_user_id(user_id)
        form = collection.find_one(
            {"formId": form_id, "userId": normalized_user_id},
            {"_id": 0},
        )
        if form:
            form["timestamp"] = serialize_datetime(form.get("timestamp"))
            form["createdAt"] = serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = serialize_datetime(form.get("updatedAt"))
        return form

    except Exception as e:
        print(f"Error retrieving form: {e}")
        return None


def fetch_form_attachments(form_id: str, user_id: str = None) -> List[dict]:
    """
    Retrieve attachments for a form.

    Args:
        form_id: The form ID.
        user_id: The user ID (required for uniqueness).

    Returns:
        List of attachment dicts, or [] if not found / unavailable.
    """
    collection = get_customer_info_collection()
    if collection is None:
        return []

    if not user_id:
        print("Warning: user_id is required to fetch form attachments")
        return []

    try:
        normalized_user_id = normalize_user_id(user_id)
        doc = collection.find_one(
            {"formId": form_id, "userId": normalized_user_id},
            {"_id": 0, "attachments": 1},
        )
        return doc.get("attachments", []) if doc else []

    except Exception as e:
        print(f"Error retrieving attachments for form {form_id}: {e}")
        return []


def create_placeholder_form(user_id: str, client_state: dict) -> Optional[str]:
    """Return the form_id for this user WITHOUT creating a MongoDB document.

    We do not eagerly insert empty 'New Form' documents — that was creating
    one empty doc per connected user, polluting the collection. Instead we
    assign the well-known DEFAULT_FORM_ID to client_state. The actual MongoDB
    document is created (via upsert) only when the first real data is saved.
    If the user already has a form in MongoDB, we find it and reuse its id.

    Args:
        user_id: The authenticated user's ID.
        client_state: Mutable dict representing this WebSocket session's state.
            ``client_state["form_id"]`` is set before returning.

    Returns:
        The assigned form_id string, or None if user_id is falsy.
    """
    if not user_id:
        return None

    # Reuse existing form if one already exists in the DB
    existing = fetch_latest_form_for_user(user_id)
    if existing:
        existing_id = existing.get("formId", DEFAULT_FORM_ID)
        client_state["form_id"] = existing_id
        print(f"[create_placeholder_form] Reusing existing form {existing_id} for user {user_id}")
        return existing_id

    # No existing form — assign DEFAULT_FORM_ID without writing to MongoDB.
    # The document will be created when the first interview answer is saved.
    client_state["form_id"] = DEFAULT_FORM_ID
    print(
        f"[create_placeholder_form] Assigned form_id {DEFAULT_FORM_ID} for new user "
        f"{user_id} (no DB write until first answer)"
    )
    return DEFAULT_FORM_ID
