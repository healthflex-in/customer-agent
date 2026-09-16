"""
Nodes for summary review: classify the user's response to the summary,
then handle the result (confirm, change, reports, etc.).
"""
from typing import Callable

from src.graph.state import InterviewState
from src.graph.pure_functions.summary import classify_summary_response
from src.graph.nodes.generate import required_response_before_summary


def make_classify_summary_intent_node(llm_complete: Callable[[str], str]):
    def classify_summary_intent_node(state: InterviewState) -> dict:
        user_input = state["user_input"]

        # Find the last agent summary message (contains the "Is this information correct" phrase)
        summary_text = ""
        for entry in reversed(state["history"]):
            if entry.get("role") == "agent" and "Is this information correct" in entry.get("message", ""):
                summary_text = entry["message"]
                break

        result = classify_summary_response(user_input, summary_text, llm_complete)
        return {"summary_intent": result}

    return classify_summary_intent_node


def make_handle_summary_response_node(llm_complete: Callable[[str], str]):
    def handle_summary_response_node(state: InterviewState) -> dict:
        summary_intent = state.get("summary_intent") or {}
        intent = summary_intent.get("intent")
        wants_upload = summary_intent.get("wants_upload")

        patch: dict = {}

        if intent == "new_complaint":
            # Patient revealed a new specific complaint — reopen the interview,
            # clear all "Not applicable" placeholders from complaint/pain sections,
            # and switch to specific_complaint mode so the conductor asks follow-ups.
            _na_values = {
                "not applicable", "general visit — no specific complaint",
                "not applicable — no pain reported", "not applicable — general visit",
                "not applicable — general visit.", "none",
            }
            updated_form = {
                section: dict(fields) if isinstance(fields, dict) else fields
                for section, fields in state["form"].items()
            }
            for _sec in ["Present Complaint", "Pain Assessment", "Previous Consultations"]:
                if _sec in updated_form and isinstance(updated_form[_sec], dict):
                    for _field in list(updated_form[_sec].keys()):
                        if str(updated_form[_sec].get(_field, "")).strip().lower() in _na_values:
                            updated_form[_sec][_field] = ""
            patch["form"] = updated_form
            patch["phase"] = "interviewing"
            patch["visit_context"] = "specific_complaint"
            # response_text left empty — generate_question will ask about the complaint

        elif intent == "confirm":
            required_response = required_response_before_summary(
                state, list(state["history"])
            )
            if required_response is not None:
                # An old/resumed session may reach summary with legacy blank
                # fields. Never terminally complete it until it goes through
                # the same required-field gate as fresh interviews.
                return required_response
            response_text = (
                "Thank you for confirming. Your medical information has been recorded. "
                "You can now close this page. Our team will review your information and get back to you soon."
            )
            patch["phase"] = "complete"
            patch["response_text"] = response_text
            patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]

        elif intent == "has_reports":
            if state.get("reports_uploaded"):
                response_text = (
                    "Your documents are already attached to this assessment. "
                    "There is no need to upload them again. Is the summary correct, "
                    "or would you like to change any information?"
                )
                patch["awaiting_report_upload"] = False
                patch["request_attachment"] = False
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]
            elif summary_intent.get("upload_claimed"):
                response_text = (
                    "Thank you for letting me know. I cannot verify an attached file for this assessment yet. "
                    "You can continue reviewing your summary and share the documents with your clinician. "
                    "Is the summary correct, or would you like to change anything?"
                )
                patch["awaiting_report_upload"] = False
                patch["request_attachment"] = False
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]
            elif wants_upload is False:
                response_text = "You can share your reports directly with your clinician. Is the summary correct, or would you like to change anything?"
                patch["awaiting_report_upload"] = False
                patch["request_attachment"] = False
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]
            elif wants_upload:
                response_text = (
                    "Great! Since you mentioned you have reports, would you like to upload them? "
                    "You can upload MRI, X-ray, CT scan, or blood test reports."
                )
                patch["awaiting_report_upload"] = True
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]
            else:
                # Ask whether they want to upload
                response_text = (
                    "Great! Since you mentioned you have reports, would you like to upload them? "
                    "You can upload MRI, X-ray, CT scan, or blood test reports."
                )
                patch["awaiting_report_upload"] = True
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]

        elif intent == "no_reports":
            # Update the form to record no reports, then let edges re-route to generate_summary
            updated_form = {
                section: dict(fields) if isinstance(fields, dict) else fields
                for section, fields in state["form"].items()
            }
            if "History & Diagnostics" in updated_form and isinstance(updated_form["History & Diagnostics"], dict):
                updated_form["History & Diagnostics"]["Reports"] = (
                    "No relevant diagnostic reports available for this issue"
                )
            patch["form"] = updated_form
            # response_text stays empty — edges will route to generate_summary to re-show

        elif intent == "request_change":
            # A general rejection ("this is wrong", "I want changes") does not
            # contain enough information to update a clinical record safely.
            # Stop this turn and ask for the exact correction. A specific reply
            # on the next turn will be detected and applied.
            if not summary_intent.get("correction_text"):
                response_text = (
                    "Of course. Which information would you like to change, "
                    "and what should the correct information be?"
                )
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [
                    {"role": "agent", "message": response_text}
                ]

        elif intent == "question":
            response_text = (
                "I'm here to help. This summary reflects the medical information you shared. "
                "You can confirm it's correct, ask to change any detail, or let me know if you have diagnostic reports to add."
            )
            patch["response_text"] = response_text
            patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]

        return patch

    return handle_summary_response_node
