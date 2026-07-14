"""WebSocket messaging helpers — send_text_message, thought updates."""
import json
import re
from typing import Optional


STANCE_URL = "https://www.stance.health/"

def _clean_response(text: str) -> str:
    """Strip markdown artifacts and normalise the Stance Health URL to a clickable link."""
    if not text:
        return text
    # Convert markdown bullet points (* item or - item) to natural line breaks
    text = re.sub(r'\n\s*[\*\-]\s+', '\n• ', text)
    # Inline * bullets (e.g. "question? * Next question")
    text = re.sub(r'\s+\*\s+', ' ', text)
    # Strip bold/italic markers (**text** or *text*)
    text = re.sub(r'\*{1,3}([^\*]+)\*{1,3}', r'\1', text)
    # Strip remaining lone asterisks
    text = re.sub(r'\*', '', text)
    # Strip markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Normalize multiple spaces
    text = re.sub(r'  +', ' ', text)
    # Normalise any old/wrong URL variants to the correct URL
    for old in ('stancehealth.com', 'http://stancehealth.com', 'https://stancehealth.com'):
        text = text.replace(old, STANCE_URL)
    # Make the URL a clickable markdown link if it appears bare (not already in [text](url))
    text = re.sub(
        r'(?<!\()(?<!\[)(https://www\.stance\.health/?)(?!\))',
        r'[www.stance.health](' + STANCE_URL + r')',
        text
    )
    return text.strip()


NODE_THOUGHTS: dict[str, dict[str, str]] = {
    "handle_first_turn":    {"stage": "Starting Interview",          "detail": "Initiating the consultation..."},
    "extract_form_data":    {"stage": "Extracting Information",      "detail": "Reading your responses and organizing patient data..."},
    "classify_intent":      {"stage": "Understanding Intent",        "detail": "Analyzing the purpose and context of your response..."},
    "validate_section":     {"stage": "Validating Completeness",     "detail": "Checking which sections have sufficient information..."},
    "advance_section":      {"stage": "Advancing Assessment",        "detail": "Moving to the next area of clinical assessment..."},
    "detect_correction":    {"stage": "Detecting Corrections",       "detail": "Checking if you're updating previous information..."},
    "apply_correction":     {"stage": "Applying Corrections",        "detail": "Updating your records with the corrected information..."},
    "generate_question":    {"stage": "Formulating Question",        "detail": "Preparing the next targeted clinical question..."},
    "generate_summary":     {"stage": "Generating Summary",          "detail": "Compiling a comprehensive summary of all your responses..."},
    "classify_summary_intent": {"stage": "Reviewing Feedback",       "detail": "Understanding your response to the summary..."},
    "handle_summary_response": {"stage": "Processing Summary Response", "detail": "Handling your confirmation or requested changes..."},
    "handle_upload_response":  {"stage": "Processing Upload",        "detail": "Handling your document upload or file response..."},
}


async def _send_thought_update(websocket, completed_nodes: list, active_node: Optional[str] = None) -> None:
    thoughts = []
    for node in completed_nodes:
        if node in NODE_THOUGHTS:
            info = NODE_THOUGHTS[node]
            thoughts.append({"stage": info["stage"], "detail": info["detail"], "status": "done"})
    if active_node and active_node in NODE_THOUGHTS:
        info = NODE_THOUGHTS[active_node]
        thoughts.append({"stage": info["stage"], "detail": info["detail"], "status": "active"})
    try:
        await websocket.send_text(json.dumps({"type": "thought_update", "thoughts": thoughts}))
    except Exception:
        pass


async def send_text_message(
    websocket,
    client_state: dict,
    text: str,
    interview_state: Optional[dict] = None,
    user_response: Optional[str] = None,
    force_request_attachment: bool = False,
) -> None:
    """Send a text_message WebSocket payload to the client."""
    text = _clean_response(text)
    requires_attachment = force_request_attachment
    if not requires_attachment and interview_state:
        # Simple check: only request attachment if form hasn't been uploaded yet
        # and the text indicates an upload prompt
        lower = (text or "").lower()
        requires_attachment = (
            "upload button" in lower or "attach them" in lower
        ) and not client_state.get("graph_reports_uploaded", False)

    payload = {
        "type": "text_message",
        "text": text,
        "session_id": client_state.get("session_id", ""),
        "interview_state": interview_state or {},
        "request_attachment": requires_attachment,
    }
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as e:
        err = str(e)
        if any(c in err for c in ("1001", "1000", "going away", "ConnectionClosed")):
            pass  # client navigated away — normal
        else:
            print(f"[messaging] send_text_message error: {e}")
