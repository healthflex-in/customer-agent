"""Persistence for customer-info intake documents.

The saved document is the contract the context-layer recommender reads.
These fields must stay stable: userId, formId, attemptId, form_data,
appointmentId, status, createdAt, and updatedAt.
"""

import copy
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import unquote, urlparse

from bson import ObjectId

from app.config import DEFAULT_FORM_ID, MONGO_DB_NAME
from app.content_safety import scrub_internal_transcription_prompts
from app.db.mongo import init_mongo, mongo
from app.db.queries import build_user_form_queries
from app.db.serializers import normalize_user_id, serialize_datetime
from app.forms.attempts import add_attempt_write_scope, resolve_form_attempt_id
from app.forms.lifecycle import build_lifecycle_update_filter, resolve_form_lifecycle
from app.forms.progress import calculate_form_progress
from app.forms.titles import build_form_title
from app.observability.logging import log as _log
from app.observability.privacy import error_type, pseudonymous_id
from app.uploads.attachments import attachment_urls

# Fixed questionnaire ID; attemptId distinguishes repeat intakes per user.
# DEFAULT_FORM_ID moved to app.config (imported above).

def resolve_assessment_appointment_id(user_id: str):
    """The booked assessment this intake belongs to.

    Priority: the appointmentId propagated onto the patient's tagged-questions doc
    (resolved at tag time in clinical-mcp) → else resolve the next BOOKED appointment
    directly from the appointments collection. Returns an ObjectId or None.
    """
    # 1) Propagated from tag time (clinical-mcp).
    try:
        if mongo.tagged_questions_collection is not None:
            td = mongo.tagged_questions_collection.find_one(
                {"$or": [{"userId": str(user_id)}, {"userId": normalize_user_id(user_id)}]},
                {"appointmentId": 1},
            )
            if td and td.get("appointmentId"):
                try:
                    return ObjectId(str(td["appointmentId"]))
                except Exception:
                    return td["appointmentId"]
    except Exception as e:
        print(f"[appointment-tag] Tagged-question lookup failed: {error_type(e)}")

    # 2) Fallback: resolve the next booked appointment ourselves.
    try:
        if mongo.mongo_client is None:
            return None
        appts = mongo.mongo_client[MONGO_DB_NAME]["appointments"]
        pid_variants = []
        try:
            pid_variants.append(ObjectId(str(user_id)))
        except Exception:
            pass
        pid_variants.append(str(user_id))
        now = datetime.now(timezone.utc)
        for pid in pid_variants:
            doc = appts.find_one(
                {"patient": pid, "status": "BOOKED", "appointmentStartTime": {"$gte": now}},
                sort=[("appointmentStartTime", 1)], projection={"_id": 1},
            )
            if doc:
                return doc["_id"]
        for pid in pid_variants:
            doc = appts.find_one(
                {"patient": pid, "status": "BOOKED"},
                sort=[("appointmentStartTime", -1)], projection={"_id": 1},
            )
            if doc:
                return doc["_id"]
    except Exception as e:
        print(f"[appointment-tag] Appointment lookup failed: {error_type(e)}")
    return None


def save_for_graph(user_id, form, section, form_id, chat_history=None,
                    preferred_doc_id=None, lifecycle_status=None,
                    attempt_id=None):
    # NOTE: preferred_doc_id must be passed in by the caller — this function
    # runs at module scope (and inside background threads) where the per-
    # connection `client_state` is NOT in scope. Referencing it here raised
    # NameError on every call, silently dropping the FRM-01 intake save.
    return save_customer_info(user_id=user_id, form_data=form,
                              current_section=section, form_id=form_id,
                              chat_history=chat_history,
                              preferred_doc_id=preferred_doc_id,
                              lifecycle_status=lifecycle_status,
                              attempt_id=attempt_id)


def save_customer_info(
    user_id: str,
    form_data: dict,
    current_section: str,
    form_id: str = None,
    attachments: Optional[List[dict]] = None,
    chat_history: Optional[list] = None,
    preferred_doc_id: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    attempt_id: Optional[str] = None,
    prom_snapshot: Optional[dict] = None,
):
    """
    Save or update customer interview information in MongoDB.
    Forms are unique by ``(userId, formId, attemptId)`` for new records.
    Legacy records without ``attemptId`` remain updateable for compatibility.
    
    Args:
        user_id: The user ID from the selected user (required for uniqueness)
        form_data: The form data dictionary
        current_section: Current section of the interview
        form_id: Optional form ID. If None, uses DEFAULT_FORM_ID.
        lifecycle_status: Explicit lifecycle transition, currently ``completed``
            for terminal interview/PROM events. Ordinary saves infer draft or
            in-progress from the form data.
        attempt_id: Intake-instance identity. When omitted, the legacy
            ``(userId, formId)`` record is selected.
    
    Returns:
        The form_id of the saved form (always DEFAULT_FORM_ID)
    """
    
    if mongo.customer_info_collection is None:
        # Try to reconnect once
        init_mongo()
    
    if mongo.customer_info_collection is None:
        print("Warning: customer-info collection not available. Skipping MongoDB save.")
        return None
    
    if not user_id:
        print("Error: user_id is required for saving form data")
        return None
    
    try:
        # Normalize user_id to ObjectId where possible
        normalized_user_id = normalize_user_id(user_id)

        # Use fixed form ID (same for all users)
        if not form_id:
            form_id = DEFAULT_FORM_ID
            print("[save_customer_info] Using default form type")
        else:
            # If form_id is provided, use it (but typically should be DEFAULT_FORM_ID)
            print("[save_customer_info] Using requested form type")
        
        # CRITICAL: Deep copy form_data to prevent shared references
        # This ensures each form has its own independent copy of the data
        form_data_copy, removed_prompt_values = scrub_internal_transcription_prompts(
            copy.deepcopy(form_data)
        )
        if removed_prompt_values:
            _log.warning(
                "internal_prompt_content_removed_before_persistence",
                removed_values=removed_prompt_values,
                subject=pseudonymous_id(user_id),
            )
        
        # Deterministic title generation avoids an external model request on
        # every save while preserving the existing empty-form lifecycle marker.
        form_title = build_form_title(form_data_copy, empty_title="New Form")
        
        # Prepare the document matching the medical_interview_history.json structure
        save_time = datetime.now(timezone.utc)
        customer_doc = {
            "userId": normalized_user_id,
            "formId": form_id,
            "title": form_title,
            "timestamp": save_time.isoformat(),
            "form_data": form_data_copy,  # Use the deep copy
            "current_section": current_section,
            "updatedAt": save_time,
        }
        if attempt_id is not None:
            normalized_attempt_id = str(attempt_id).strip()
            if not normalized_attempt_id:
                raise ValueError("attempt_id cannot be empty")
            customer_doc["attemptId"] = normalized_attempt_id
        if prom_snapshot is not None:
            customer_doc["promSnapshot"] = copy.deepcopy(prom_snapshot)

        # Tag this intake to the patient's booked assessment so the recommender's
        # customer icon can fetch the right assessment's data. Only set when
        # resolved, so we never clobber an existing tag with null.
        _appt_id = resolve_assessment_appointment_id(user_id)
        if _appt_id is not None:
            customer_doc["appointmentId"] = _appt_id

        # Find existing doc — prefer preferred_doc_id (template doc _id) when provided,
        # then try both ObjectId and plain-string userId so we don't create a second
        # document when external systems stored userId as a string.
        existing = None
        if preferred_doc_id:
            try:
                existing = mongo.customer_info_collection.find_one({"_id": ObjectId(preferred_doc_id)})
            except Exception:
                pass
        if not existing:
            _uid_variants = [normalized_user_id]
            try:
                _uid_variants.append(str(user_id))
            except Exception:
                pass
            for _uid in _uid_variants:
                _existing_query = add_attempt_write_scope(
                    {"formId": form_id, "userId": _uid},
                    attempt_id,
                )
                existing = mongo.customer_info_collection.find_one(_existing_query)
                if existing:
                    break

        lifecycle = resolve_form_lifecycle(
            form_data_copy,
            requested_status=lifecycle_status,
            existing_status=existing.get("status") if existing else None,
            existing_completed_at=existing.get("completedAt") if existing else None,
            now=save_time,
        )
        customer_doc["status"] = lifecycle.status
        if lifecycle.expires_at is not None:
            customer_doc["expiresAt"] = lifecycle.expires_at
        if lifecycle.completed_at is not None:
            customer_doc["completedAt"] = lifecycle.completed_at

        attachments_to_store = (
            attachments if attachments is not None else
            (existing.get("attachments", []) if existing else [])
        )
        customer_doc["attachments"] = attachment_urls(attachments_to_store)

        if existing:
            # Update the doc we found (regardless of how userId was stored)
            update = {"$set": customer_doc}
            if lifecycle.expires_at is None:
                update["$unset"] = {"expiresAt": ""}
            update_filter = build_lifecycle_update_filter(
                existing["_id"],
                requested_status=lifecycle_status,
            )
            mongo.customer_info_collection.update_one(update_filter, update)
        else:
            customer_doc["createdAt"] = save_time
            mongo.customer_info_collection.insert_one(customer_doc)
        print("[save_customer_info] Customer form persisted")
        
        return form_id
            
    except Exception as e:
        print(f"[save_customer_info] Persistence failed: {error_type(e)}")
        return None


def fetch_user_forms(user_id: str) -> list:
    """
    Retrieve all forms for a specific user from MongoDB.
    
    Args:
        user_id: The user ID to retrieve forms for
        
    Returns:
        List of form documents with formId, title, timestamp, etc.
    """
    
    if mongo.customer_info_collection is None:
        init_mongo()
    
    if mongo.customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return []
    
    try:
        queries = build_user_form_queries(user_id, DEFAULT_FORM_ID)
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        forms = list(
            mongo.customer_info_collection.find(
                identity_query,
                {"_id": 0}
            ).sort("createdAt", -1)
        )
        
        formatted_forms = []
        for form in forms:
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_form_list_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
            formatted_forms.append({
                "formId": form.get("formId", ""),
                "attemptId": form.get("attemptId"),
                "title": form.get("title", "Untitled Form"),
                "timestamp": serialize_datetime(form.get("timestamp")),
                "createdAt": serialize_datetime(form.get("createdAt")),
                "updatedAt": serialize_datetime(form.get("updatedAt")),
                "current_section": form.get("current_section", ""),
                "progress": calculate_form_progress(form.get("form_data", {}))
            })
        
        return formatted_forms
    except Exception as e:
        print(f"[fetch_user_forms] Retrieval failed: {error_type(e)}")
        return []


def fetch_latest_form_for_user(user_id: str) -> dict:
    """
    Retrieve the latest default-form attempt for a specific user.
    """

    if mongo.customer_info_collection is None:
        init_mongo()

    if mongo.customer_info_collection is None:
        print("Warning: customer-info collection not available when fetching latest form.")
        return None

    try:
        queries = build_user_form_queries(user_id, DEFAULT_FORM_ID)
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        form = mongo.customer_info_collection.find_one(
            identity_query,
            {"_id": 0},
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        if form:
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_latest_form_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
            form["timestamp"] = serialize_datetime(form.get("timestamp"))
            form["createdAt"] = serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"[fetch_latest_form] Retrieval failed: {error_type(e)}")
        return None


def fetch_form_by_id(
    form_id: str,
    user_id: str = None,
    attempt_id: Optional[str] = None,
) -> dict:
    """
    Retrieve an exact attempt, or the latest matching legacy-compatible form.
    
    Args:
        form_id: The form ID to retrieve
        user_id: The user ID (required for uniqueness)
        attempt_id: Optional intake-instance ID. Older clients may omit it and
            receive the latest matching attempt.
        
    Returns:
        Form document or None if not found
    """
    
    if mongo.customer_info_collection is None:
        init_mongo()
    
    if mongo.customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return None
    
    if not user_id:
        print("Warning: user_id is required to fetch form by form_id")
        return None
    
    try:
        # Try ObjectId first, then plain string — external systems may store userId
        # as a string while our backend normalises it to ObjectId.
        _uid_variants = [user_id]
        try:
            _uid_variants.insert(0, ObjectId(user_id))
        except Exception:
            pass
        _queries = []
        for _uid in _uid_variants:
            _query = {"formId": form_id, "userId": _uid}
            if attempt_id is not None:
                _normalized_attempt = str(attempt_id).strip()
                if not _normalized_attempt:
                    raise ValueError("attempt_id cannot be empty")
                _query["attemptId"] = _normalized_attempt
            _queries.append(_query)
        _identity_query = _queries[0] if len(_queries) == 1 else {"$or": _queries}
        form = mongo.customer_info_collection.find_one(
            _identity_query,
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        if form:
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_form_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
            form["_id"] = str(form.get("_id", ""))
            form["timestamp"] = serialize_datetime(form.get("timestamp"))
            form["createdAt"] = serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"[fetch_form] Retrieval failed: {error_type(e)}")
        return None


def fetch_form_attachments(
    form_id: str,
    user_id: str = None,
    attempt_id: Optional[str] = None,
) -> List[dict]:
    """
    Retrieve attachments for a form.
    The user and optional attempt identity scope the requested form.
    """

    if mongo.customer_info_collection is None:
        init_mongo()

    if mongo.customer_info_collection is None:
        return []

    if not user_id:
        print("Warning: user_id is required to fetch form attachments")
        return []

    try:
        queries = build_user_form_queries(
            user_id,
            form_id,
            attempt_id=attempt_id,
        )
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        doc = mongo.customer_info_collection.find_one(
            identity_query,
            {"_id": 0, "attachments": 1},
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        # Patient UI uses metadata records; MongoDB/dashboard stores URL strings.
        # Preserve the existing REST/WS shape while accepting both DB formats.
        return [
            item if isinstance(item, dict) else {
                "url": item, "type": "report", "label": "Medical Report",
                "fileName": unquote(urlparse(item).path.rsplit("/", 1)[-1]),
            }
            for item in (doc.get("attachments", []) if doc else [])
            if isinstance(item, (str, dict))
        ]
    except Exception as e:
        print(f"[fetch_attachments] Retrieval failed: {error_type(e)}")
        return []


def create_placeholder_form(
    user_id: str,
    client_state: dict,
    preferred_attempt_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve or durably reserve the first intake attempt for a patient.

    A newly allocated identity is persisted as an empty ``draft`` immediately.
    Draft TTL cleanup prevents abandoned reservations from accumulating, while
    persistence guarantees that a browser refresh can safely resume the same
    attempt. Repeat intakes are still never allocated here; they must be
    assigned by the consultant workflow.
    """
    # Check if a form already exists — if so, reuse its exact attempt.
    existing = None if preferred_attempt_id else fetch_latest_form_for_user(user_id)
    if existing:
        existing_id = existing.get("formId", DEFAULT_FORM_ID)
        client_state["form_id"] = existing_id
        client_state["attempt_id"] = resolve_form_attempt_id(existing)
        print("[create_placeholder_form] Reusing existing form")
        return existing_id

    # No existing form: allocate (or recover) the first attempt and persist an
    # expiring draft so the identity remains valid across reloads.
    client_state["form_id"] = DEFAULT_FORM_ID
    client_state["attempt_id"] = (
        str(preferred_attempt_id).strip()
        if preferred_attempt_id
        else resolve_form_attempt_id(existing)
    )
    if not client_state["attempt_id"]:
        raise ValueError("Attempt ID cannot be empty")

    saved_form_id = save_customer_info(
        user_id=user_id,
        form_data={},
        current_section="Present Complaint",
        form_id=DEFAULT_FORM_ID,
        attempt_id=client_state["attempt_id"],
    )
    if not saved_form_id:
        raise RuntimeError("Unable to reserve the new assessment attempt")
    print("[create_placeholder_form] Persisted expiring draft attempt")
    return DEFAULT_FORM_ID

