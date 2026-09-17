"""
Nodes for summary review: classify the user's response to the summary,
then handle the result (confirm, change, reports, etc.).
"""
from typing import Callable

from src.graph.state import InterviewState
from src.graph.pure_functions.summary import classify_summary_response, _fallback_summary, reports_with_verified_upload
from src.graph.nodes.generate import required_response_before_summary


def make_classify_summary_intent_node(llm_complete: Callable[[str], str]):
    def classify_summary_intent_node(state: InterviewState) -> dict:
        user_input = state["user_input"]

        # Historical summaries describe the record before later corrections.
        # Classify against current structured facts, so old key points cannot
        # steer a subsequent correction or approval back to outdated details.
        summary_text = _fallback_summary(state["form"])

        result = classify_summary_response(user_input, summary_text, llm_complete)
        history = list(state["history"])
        history.append({"role": "user", "message": user_input})
        return {"summary_intent": result, "history": history}

    return classify_summary_intent_node


def make_handle_summary_response_node(llm_complete: Callable[[str], str]):
    def handle_summary_response_node(state: InterviewState) -> dict:
        summary_intent = state.get("summary_intent") or {}
        intent = summary_intent.get("intent")
        wants_upload = summary_intent.get("wants_upload")

        patch: dict = {}

        if intent == "new_complaint":
            from src.graph.pure_functions.additional_complaint import capture_additional_complaint
            from src.graph.pure_functions.question_plan import question_for_missing_fields
            updated_form, added_section = capture_additional_complaint(
                state["form"], state["user_input"]
            )
            missing = [(added_section, field) for field, value in updated_form[added_section].items() if not value]
            response_text = (
                "I've added what you just shared alongside your earlier concern: "
                + state["user_input"].strip()
                + "\n\nFor this additional concern, "
                + question_for_missing_fields(missing, {})
            )
            return {
                "form": updated_form,
                "form_sections": list(updated_form),
                "current_section": added_section,
                "missing_fields": [field for _, field in missing],
                "phase": "interviewing",
                "visit_context": "specific_complaint",
                "response_text": response_text,
                "history": state["history"] + [{"role": "agent", "message": response_text}],
            }
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
            updated_form = {section: dict(fields) if isinstance(fields, dict) else fields
                            for section, fields in state["form"].items()}
            diagnostics = updated_form.setdefault("History & Diagnostics", {})
            if state.get("reports_uploaded"):
                diagnostics["Reports"] = reports_with_verified_upload(diagnostics.get("Reports", ""))
            elif not summary_intent.get("upload_claimed"):
                diagnostics["Reports"] = "Patient reports having diagnostic documents; upload not yet verified"
            patch["form"] = updated_form
            if state.get("reports_uploaded") and summary_intent.get("additional_reports"):
                diagnostics["Reports"] += "; patient has additional reports to share"
            if summary_intent.get("additional_reports") and wants_upload is not False:
                response_text = (
                    "You can upload the additional report here. Any documents you previously "
                    "uploaded will remain attached to this assessment."
                )
                patch.update(awaiting_report_upload=True, request_attachment=True,
                             response_text=response_text,
                             history=state["history"] + [{"role": "agent", "message": response_text}])
            elif state.get("reports_uploaded") and wants_upload is False and not summary_intent.get("upload_claimed"):
                response_text = "Your uploaded documents remain attached. You can share the additional reports directly with your clinician. Is the summary otherwise correct?"
                patch.update(awaiting_report_upload=False, request_attachment=False,
                             response_text=response_text,
                             history=state["history"] + [{"role": "agent", "message": response_text}])
            elif state.get("reports_uploaded"):
                response_text = (
                    "The documents you just uploaded are already attached to this assessment. "
                    "I've updated your report information to reflect the upload. Is the summary otherwise correct, "
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
                patch["request_attachment"] = True
                patch["response_text"] = response_text
                patch["history"] = state["history"] + [{"role": "agent", "message": response_text}]
            else:
                # Ask whether they want to upload
                response_text = (
                    "Great! Since you mentioned you have reports, would you like to upload them? "
                    "You can upload MRI, X-ray, CT scan, or blood test reports."
                )
                patch["awaiting_report_upload"] = True
                patch["request_attachment"] = True
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
                    "Diagnostic documents uploaded and attached; no additional reports available"
                    if state.get("reports_uploaded") else
                    "No relevant diagnostic reports available for this issue"
                )
            patch["form"] = updated_form
            response_text = "I've noted your report information. Is the summary otherwise correct, or would you like to change anything?"
            patch.update(response_text=response_text, awaiting_report_upload=False,
                         request_attachment=False,
                         history=state["history"] + [{"role": "agent", "message": response_text}])

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
