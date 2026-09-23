"""
Node factory functions for correction detection and application.
"""
from src.graph.state import InterviewState
from src.graph.pure_functions.form_extraction import detect_form_correction, apply_form_correction


def make_detect_correction_node(llm_complete):
    def detect_correction_node(state: InterviewState) -> dict:
        user_input = state["user_input"]
        form = state["form"]
        phase = state["phase"]
        is_summary_mode = (phase == "summary")
        from src.graph.pure_functions.complaint_severity import explicit_severity_updates
        updates = explicit_severity_updates(form, user_input)
        if updates:
            return {"correction_data": {"severity_updates": updates}}
        result = detect_form_correction(
            user_input, form, is_summary_mode=is_summary_mode, llm_complete=llm_complete
        )
        return {"correction_data": result}

    return detect_correction_node


def make_apply_correction_node(llm_complete):
    def apply_correction_node(state: InterviewState) -> dict:
        correction_data = state["correction_data"]

        if correction_data is None:
            response_text = (
                "I couldn't identify the exact change. Please tell me which information "
                "is incorrect and what the correct information should be."
            )
            return {
                "correction_applied": False,
                "response_text": response_text,
                "history": list(state["history"]) + [
                    {"role": "agent", "message": response_text}
                ],
            }

        form = state["form"]
        history = list(state["history"])

        if "severity_updates" in correction_data:
            import copy
            updated_form = copy.deepcopy(form)
            changes = []
            for (section, field), score in correction_data["severity_updates"].items():
                updated_form[section][field] = score
                description = (updated_form[section].get("Primary Complaint")
                               if section.startswith("Additional Complaint ") else
                               updated_form.get("Pain Assessment", {}).get("Primary Location of Pain")
                               or updated_form.get("Present Complaint", {}).get("Primary Complaint"))
                changes.append(f"{description}: {score}")
            message = "I've updated the pain ratings separately: " + "; ".join(changes) + ". Would you like to update anything else? If everything is correct, you can confirm."
            return {"form": updated_form, "correction_applied": True,
                    "response_text": message,
                    "history": history + [{"role": "agent", "message": message}]}

        updated_form, success, message = apply_form_correction(correction_data, form, llm_complete)

        updated_history = history + [{"role": "agent", "message": message}]

        return {
            "form": updated_form,
            "correction_applied": success,
            "response_text": message,
            "history": updated_history,
        }

    return apply_correction_node
