"""Deterministic progress labels sent to the interview UI. These are not model thoughts."""

import json

from fastapi import WebSocket


NODE_THOUGHTS: dict[str, dict[str, str]] = {
    "handle_first_turn": {
        "stage": "Starting Interview",
        "detail": "Initiating the consultation and reviewing your case...",
    },
    "extract_form_data": {
        "stage": "Extracting Information",
        "detail": "Reading your responses and organizing patient data...",
    },
    "validate_section": {
        "stage": "Validating Completeness",
        "detail": "Checking which sections have sufficient information...",
    },
    "advance_section": {
        "stage": "Advancing Assessment",
        "detail": "Moving to the next area of clinical assessment...",
    },
    "detect_correction": {
        "stage": "Detecting Corrections",
        "detail": "Checking if you're updating or correcting previous information...",
    },
    "apply_correction": {
        "stage": "Applying Corrections",
        "detail": "Updating your records with the corrected information...",
    },
    "generate_question": {
        "stage": "Formulating Question",
        "detail": "Preparing the next targeted clinical question...",
    },
    "generate_summary": {
        "stage": "Generating Summary",
        "detail": "Compiling a comprehensive summary of all your responses...",
    },
    "classify_summary_intent": {
        "stage": "Reviewing Feedback",
        "detail": "Understanding your response to the summary...",
    },
    "handle_summary_response": {
        "stage": "Processing Summary Response",
        "detail": "Handling your confirmation or requested changes...",
    },
    "handle_upload_response": {
        "stage": "Processing Upload",
        "detail": "Handling your document upload or file response...",
    },
}


async def send_thought_update(
    websocket: WebSocket,
    completed_nodes: list[str],
    active_node: str | None = None,
) -> None:
    """Send a thought_update message to the frontend with the current thought list."""
    thoughts: list[dict[str, str]] = []
    for node in completed_nodes:
        info = NODE_THOUGHTS[node]
        thoughts.append({
            "stage": info["stage"],
            "detail": info["detail"],
            "status": "done",
        })
    if active_node:
        info = NODE_THOUGHTS[active_node]
        thoughts.append({
            "stage": info["stage"],
            "detail": info["detail"],
            "status": "active",
        })
    try:
        await websocket.send_text(json.dumps({
            "type": "thought_update",
            "thoughts": thoughts,
        }))
    except Exception:
        pass  # WebSocket closed — safe to ignore
