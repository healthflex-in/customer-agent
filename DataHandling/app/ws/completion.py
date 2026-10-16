"""Read-only response for an assessment that is already completed."""

import json

from fastapi import WebSocket

from app.audit.chat_history import record_chat_message
from app.config import CHAT_HISTORY_RETENTION_DAYS
from app.db.mongo import mongo
from app.db.serializers import serialize_datetime
from app.forms.lifecycle import COMPLETED as FORM_COMPLETED
from app.runtime.blocking import run_blocking


COMPLETED_FORM_MESSAGE = (
    "This assessment has already been completed and is now read-only. "
    "If another assessment is needed, your clinician will send you a new assessment link."
)


def build_completed_form_payload(client_state: dict, form: dict) -> dict:
    """Build the deterministic, zero-AI response for a terminal attempt."""

    form_id = form.get("formId") or client_state.get("form_id")
    attempt_id = form.get("attemptId") or client_state.get("attempt_id")
    return {
        "type": "form_completed",
        "text": COMPLETED_FORM_MESSAGE,
        "session_id": client_state["session_id"],
        "formId": form_id,
        "attemptId": attempt_id,
        "status": FORM_COMPLETED,
        "completedAt": serialize_datetime(form.get("completedAt")),
        "interview_state": {
            "section": "Completed",
            "progress": 100.0,
            "missing_fields": [],
            "attachments": [],
            "formId": form_id,
            "attemptId": attempt_id,
            "sectionProgress": [],
            "promSteps": None,
            "status": FORM_COMPLETED,
            "locked": True,
        },
    }


async def send_completed_form(websocket: WebSocket, client_state: dict, form: dict) -> None:
    """Lock a completed attempt in-session and notify the patient without AI."""

    client_state["form_id"] = form.get("formId") or client_state.get("form_id")
    client_state["attempt_id"] = form.get("attemptId") or client_state.get("attempt_id")
    client_state["attempt_locked"] = True
    client_state["locked_form"] = {
        "formId": client_state.get("form_id"),
        "attemptId": client_state.get("attempt_id"),
        "status": FORM_COMPLETED,
        "completedAt": form.get("completedAt"),
    }
    client_state["is_recording"] = False
    client_state["received_audio_buffer"].clear()
    client_state["recording_start_time"] = None
    await websocket.send_text(json.dumps(build_completed_form_payload(client_state, form)))
    await run_blocking(
        record_chat_message,
        mongo.chat_history_collection,
        user_id=client_state.get("user_id"),
        session_id=client_state.get("session_id"),
        form_id=client_state.get("form_id"),
        attempt_id=client_state.get("attempt_id"),
        interview_id=client_state.get("interview_id"),
        role="agent",
        content=COMPLETED_FORM_MESSAGE,
        message_type="form_completed",
        phase=FORM_COMPLETED,
        section="Completed",
        retention_days=CHAT_HISTORY_RETENTION_DAYS,
    )
