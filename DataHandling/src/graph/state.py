"""
InterviewState — the single source of truth that flows through the LangGraph graph.
Replaces the scattered self.* fields on HealthAgent.
"""
from typing import TypedDict, Optional, Literal
import copy

from src.prompts import get_medical_form_template

FormData = dict  # section -> {field: value}


class InterviewState(TypedDict):
    # ── Identity ──────────────────────────────────────────────
    user_id: str
    form_id: str
    session_id: str

    # ── Turn input ─────────────────────────────────────────────
    user_input: str

    # ── Form data ──────────────────────────────────────────────
    form: FormData

    # ── Navigation (replaces self.idx + self.current_section) ──
    question_round: int          # 0/1/2 — replaces self.idx
    current_section: str
    form_sections: list          # ordered list of all 6 sections

    # ── Phase (replaces talk_mode + 4 boolean flags) ───────────
    phase: str                   # welcome | interviewing | awaiting_upload | summary | complete

    # ── Conversation flags ──────────────────────────────────────
    asked_previous_consultations: bool
    reports_uploaded: bool
    awaiting_report_upload: bool
    attempts_on_current_section: int

    # ── Conversation history ────────────────────────────────────
    history: list                # [{"role": "agent"|"user", "message": "..."}]

    # ── Intra-turn computed signals (scratch, overwritten each turn) ──
    missing_fields: list
    summary_intent: Optional[dict]
    reports_intent: Optional[dict]
    is_correction_turn: bool
    correction_data: Optional[dict]

    # ── Orchestrator question override ─────────────────────────
    orchestrator_question_id: Optional[str]
    orchestrator_question_text: Optional[str]

    # ── Output ─────────────────────────────────────────────────
    response_text: str
    request_attachment: bool   # True when the frontend should show the upload button


def get_fresh_interview_state(user_id: str = "", form_id: str = "", session_id: str = "") -> InterviewState:
    """Return a blank InterviewState for a new interview. Replaces HealthAgent.init_form()."""
    fresh_form = copy.deepcopy(get_medical_form_template())
    form_sections = list(fresh_form.keys())
    from src.prompts import WELCOME_PROMPT
    return InterviewState(
        user_id=user_id,
        form_id=form_id,
        session_id=session_id,
        user_input="",
        form=fresh_form,
        question_round=0,
        current_section=form_sections[0],
        form_sections=form_sections,
        phase="welcome",
        asked_previous_consultations=False,
        reports_uploaded=False,
        awaiting_report_upload=False,
        attempts_on_current_section=0,
        history=[{"role": "agent", "message": WELCOME_PROMPT}],
        missing_fields=[],
        summary_intent=None,
        reports_intent=None,
        is_correction_turn=False,
        correction_data=None,
        orchestrator_question_id=None,
        orchestrator_question_text=None,
        response_text="",
        request_attachment=False,
    )
