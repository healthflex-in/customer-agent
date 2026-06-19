"""
Node for handling the user's response to the report-upload prompt.
"""
from src.graph.state import InterviewState

_UPLOAD_CONFIRM_KEYWORDS = {
    "yes", "sure", "ok", "okay", "upload", "uploaded", "uploading",
    "yeah", "yep", "yup", "done", "sent", "shared", "attached",
    "i have", "i did", "i've", "complete", "completed",
}
_DECLINE_KEYWORDS = {
    "no", "nope", "nah", "skip", "later", "not now",
    "don't have", "dont have", "do not have",
}

# Phrases that indicate the user already uploaded
_ALREADY_UPLOADED_PHRASES = [
    "have uploaded", "already uploaded", "i uploaded", "just uploaded",
    "uploaded my", "uploaded the", "done uploading", "files uploaded",
    "documents uploaded", "reports uploaded", "i've uploaded",
]


def make_handle_upload_response_node():
    def handle_upload_response_node(state: InterviewState) -> dict:
        user_input_raw = state.get("user_input", "")
        user_input = user_input_raw.lower().strip()
        awaiting = state.get("awaiting_report_upload", False)
        reports_uploaded = state.get("reports_uploaded", False)
        history = list(state["history"])
        form = state["form"]

        # ── If user already uploaded via the button (REST API set reports_uploaded=True)
        # or the form already has attachments — clear the awaiting state and continue.
        if awaiting and reports_uploaded:
            return {
                "awaiting_report_upload": False,
                "request_attachment": False,
            }

        # ── PHASE 1: First time reports are mentioned — prompt the user to upload ──
        if not awaiting:
            response_text = (
                "It looks like you have reports to share — please use the upload button "
                "below to attach them. You can upload MRI, X-ray, CT scans, blood reports, "
                "or any other relevant documents."
            )
            history.append({"role": "agent", "message": response_text})
            return {
                "awaiting_report_upload": True,
                "request_attachment": True,
                "response_text": response_text,
                "history": history,
            }

        # ── PHASE 2: User is responding to the upload prompt ──
        words = set(user_input.replace(",", " ").replace(".", " ").split())

        # Detect "I have uploaded..." and similar past-tense confirmations
        already_uploaded = any(phrase in user_input for phrase in _ALREADY_UPLOADED_PHRASES)
        user_confirmed = already_uploaded or bool(words & _UPLOAD_CONFIRM_KEYWORDS)
        user_declined = (not user_confirmed) and (
            bool(words & _DECLINE_KEYWORDS)
            or any(d in user_input for d in _DECLINE_KEYWORDS)
        )

        if user_confirmed:
            # User confirmed upload — mark as done and continue interview
            response_text = "Thank you! I've noted that your reports have been shared. Let's continue."
            history.append({"role": "agent", "message": response_text})
            return {
                "awaiting_report_upload": False,
                "reports_uploaded": True,
                "request_attachment": False,
                "response_text": response_text,
                "history": history,
            }

        if user_declined:
            # Record that no upload was provided and continue the interview
            updated_form = {
                section: dict(fields) if isinstance(fields, dict) else fields
                for section, fields in form.items()
            }
            if "History & Diagnostics" in updated_form and isinstance(updated_form["History & Diagnostics"], dict):
                if not updated_form["History & Diagnostics"].get("Reports", "").strip():
                    updated_form["History & Diagnostics"]["Reports"] = (
                        "Patient mentioned having reports but chose not to upload them."
                    )
            return {
                "form": updated_form,
                "awaiting_report_upload": False,
                "reports_uploaded": False,
                "request_attachment": False,
            }

        # Ambiguous response — show the upload button once more
        response_text = "Please use the upload button below to share your reports, or tap Skip to continue."
        history.append({"role": "agent", "message": response_text})
        return {
            "awaiting_report_upload": True,
            "request_attachment": True,
            "response_text": response_text,
            "history": history,
        }

    return handle_upload_response_node
