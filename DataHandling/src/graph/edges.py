"""
Conditional edge functions for the medical interview LangGraph.
Each function receives the current InterviewState and returns the next node name.
"""
from langgraph.graph import END
from src.graph.state import InterviewState


def route_by_phase(state: InterviewState) -> str:
    """Entry router — dispatches to the correct subgraph based on current phase."""
    return state["phase"]


def after_extract(state: InterviewState) -> str:
    """Route signals computed by the combined extraction/intent node."""
    if state.get("is_correction_turn"):
        return "detect_correction"
    # In PROM/tagged-question sessions, skip reports upload flow entirely —
    # "yes" answers are about clinical scores, not document uploads
    if state.get("tagged_turns"):
        return "validate_section"
    # Already in the upload flow — route the user's reply back to the upload node
    if state.get("awaiting_report_upload") and not state.get("reports_uploaded"):
        return "handle_upload_response"
    reports_intent = state.get("reports_intent") or {}
    if (
        reports_intent.get("has_reports") is True
        and not state.get("reports_uploaded")
    ):
        return "handle_upload_response"
    return "validate_section"


def after_validate_section(state: InterviewState) -> str:
    if state.get("missing_fields"):
        return "generate_question"
    return "advance_section"


def after_advance_section(state: InterviewState) -> str:
    if state.get("question_round", 0) >= 3:
        return "generate_summary"
    return "generate_question"


def after_apply_correction(state: InterviewState) -> str:
    if state.get("phase") == "summary":
        return "generate_summary"
    return "generate_question"


def after_handle_summary_response(state: InterviewState) -> str:
    intent = (state.get("summary_intent") or {}).get("intent")
    if intent == "confirm":
        return END
    if intent == "new_complaint":
        # Patient revealed new health info — reopen interview, ask follow-ups
        return "generate_question"
    if intent == "request_change":
        return "apply_correction"
    if intent in ("has_reports", "no_reports"):
        return END
    return "generate_question"


def after_handle_upload_response(state: InterviewState) -> str:
    # Phase 1: we just showed the upload prompt (awaiting_report_upload=True).
    # Stop here — don't overwrite the upload message with a new question.
    if state.get("awaiting_report_upload"):
        return END
    # Phase 2: user confirmed/declined — continue the interview.
    if state.get("question_round", 0) >= 3:
        return "generate_summary"
    return "generate_question"
