"""Outbound interview text sent over the patient socket."""

import json
from typing import Optional

from fastapi import WebSocket

from app.audit.chat_history import record_client_chat
from app.forms.lifecycle import COMPLETED as FORM_COMPLETED
from app.forms.repository import fetch_form_by_id
from app.observability.privacy import error_type
from app.runtime.blocking import run_blocking
from app.ws.state import build_interview_state_async, form_fallback_agent


def message_requires_attachment(
    current_section: Optional[str], 
    message_text: Optional[str] = None, 
    form_id: Optional[str] = None,
    user_response: Optional[str] = None,
    user_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
) -> bool:
    """
    Show upload card ONLY if:
    1. We're in History & Diagnostics section AND
    2. The user's previous response indicated they HAVE reports (not when asking the question)
    
    Args:
        current_section: Current section name
        message_text: The assistant's message text (for logging)
        form_id: Form ID to check existing attachments
        user_response: The user's response text (to check if they said they have reports)
        user_id: User ID (required to fetch form by formId + userId combination)
    """
    try:
        # If we already have reports attachments for this form, don't ask again
        if form_id and user_id:
            form = fetch_form_by_id(form_id, user_id, attempt_id)
            if form:
                attachments = form.get("attachments", [])
                has_attachments = bool(attachments)
                if has_attachments:
                    print(f"[message_requires_attachment] Attachments already present, not showing upload")
                    return False
    except Exception as e:
        print(f"[message_requires_attachment] Attachment check failed: {error_type(e)}")

    # If the agent is explicitly asking the user to upload reports, always request attachment
    # This is the follow-up prompt like:
    # "Great! Since you mentioned you have reports, would you like to upload them? ..."
    if message_text:
        lower_msg = (message_text if isinstance(message_text, str) else str(message_text)).lower()
        if "would you like to upload" in lower_msg or ("upload them" in lower_msg and "reports" in lower_msg):
            print("[message_requires_attachment] Agent is explicitly asking to upload reports, showing upload card")
            return True

    # CRITICAL: Only show upload if:
    # 1. We're in History & Diagnostics section
    # 2. The agent explicitly asks to upload reports
    #    (user intent about reports is now handled by the LLM inside HealthAgent,
    #     which generates the precise upload prompt text when appropriate).
    if current_section and "History & Diagnostics" in current_section:
        # If this is the initial diagnostic question, do NOT show upload yet
        if message_text:
            question_phrases = [
                "do you have any mri",
                "x-ray",
                "ct scan",
                "blood reports",
                "do you have any reports",
                "any reports related",
            ]
            if any(phrase in message_text.lower() for phrase in question_phrases):
                print("[message_requires_attachment] Agent is asking about reports, not showing upload card yet")
                return False
        else:
            # No user response yet (this is the question being asked), don't show upload
            print(f"[message_requires_attachment] Asking about reports, not showing upload yet (waiting for user response)")
            return False
    
    return False


async def send_text_message(
    websocket: WebSocket,
    client_state: dict,
    text: str,
    interview_state: Optional[dict] = None,
    user_response: Optional[str] = None,
    force_request_attachment: bool = False,
    question_meta: Optional[dict] = None,
    audit_message_type: str = "text_message",
    audit_event_key: Optional[str] = None,
):
    """
    Send a text message to the client.

    Args:
        websocket: WebSocket connection
        client_state: Current client state
        text: Message text to send
        interview_state: Optional interview state
        user_response: Optional user's previous response (to check if they have reports)
        force_request_attachment: If True, always show the upload button regardless of heuristics
        audit_message_type: Chat-history type for this agent message (e.g. "resume_state")
        audit_event_key: Stable key so a repeated event is stored only once
    """
    if interview_state and interview_state.get("status") == FORM_COMPLETED:
        # Completion during an active session uses a final text_message. Lock
        # server-side too, so already queued patient messages cannot call AI.
        client_state["attempt_locked"] = True
        client_state["locked_form"] = {
            "formId": client_state.get("form_id"),
            "attemptId": client_state.get("attempt_id"),
            "status": FORM_COMPLETED,
        }
        requires_attachment = False
    elif force_request_attachment:
        requires_attachment = True
    else:
        current_section = interview_state.get("section") if interview_state else form_fallback_agent().current_section
        requires_attachment = await run_blocking(
            message_requires_attachment,
            current_section,
            text,
            client_state.get("form_id"),
            user_response=user_response,
            user_id=client_state.get("user_id"),
            attempt_id=client_state.get("attempt_id"),
        )

    resolved_interview_state = (
        interview_state
        if interview_state is not None
        else await build_interview_state_async(client_state)
    )

    payload = {
        "type": "text_message",
        "text": text,
        "session_id": client_state["session_id"],
        "interview_state": resolved_interview_state,
        "request_attachment": requires_attachment,
    }
    if question_meta and question_meta.get("type") and question_meta.get("type") != "text":
        payload["question_meta"] = question_meta
    await websocket.send_text(json.dumps(payload))
    await run_blocking(
        record_client_chat,
        client_state,
        role="agent",
        content=text,
        message_type=audit_message_type,
        event_key=audit_event_key,
        question_id=(question_meta or {}).get("questionId"),
        phase=resolved_interview_state.get("status"),
        section=resolved_interview_state.get("section"),
    )

