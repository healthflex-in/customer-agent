"""
Nodes for summary review: classify the user's response to the summary,
then handle the result (confirm, change, reports, etc.).
"""
from typing import Callable

from src.graph.state import InterviewState
from src.graph.pure_functions.summary import classify_summary_response


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
            response_text = (
                "Thank you for confirming. Your medical information has been recorded. "
                "You can now close this page. Our team will review your information and get back to you soon."
            )
            patch["phase"] = "complete"
            patch["response_text"] = response_text
            patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]

        elif intent == "has_reports":
            if wants_upload:
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
            # response_text stays empty — edges will route to apply_correction
            pass

        elif intent == "question":
            response_text = (
                "I'm here to help. This summary reflects the medical information you shared. "
                "You can confirm it's correct, ask to change any detail, or let me know if you have diagnostic reports to add."
            )
            patch["response_text"] = response_text
            patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]

        return patch

    return handle_summary_response_node
