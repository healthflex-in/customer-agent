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
        result = detect_form_correction(
            user_input, form, is_summary_mode=is_summary_mode, llm_complete=llm_complete
        )
        return {"correction_data": result}

    return detect_correction_node


def make_apply_correction_node(llm_complete):
    def apply_correction_node(state: InterviewState) -> dict:
        correction_data = state["correction_data"]

        if correction_data is None:
            return {"response_text": "I couldn't identify what to change. Could you be more specific?"}

        form = state["form"]
        history = list(state["history"])

        updated_form, success, message = apply_form_correction(correction_data, form, llm_complete)

        updated_history = history + [{"role": "agent", "message": message}]

        return {
            "form": updated_form,
            "response_text": message,
            "history": updated_history,
        }

    return apply_correction_node
