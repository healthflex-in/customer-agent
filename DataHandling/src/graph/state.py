"""
InterviewState — the single source of truth that flows through the LangGraph graph.
Replaces the scattered self.* fields on HealthAgent.
"""
from typing import TypedDict, Optional, Literal

from src.forms.loader import load_form as _load_form

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
    referral_asked: bool           # True once the referral/Source question has been asked
    visit_context: str             # "specific_complaint" | "general_assessment" | "unknown"

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

    # ── Pre-computed next question (set by extract, consumed by generate) ──
    pending_question: Optional[str]

    # ── MCP question recommendations (dev only, set on first turn) ──────────
    mcp_questions: Optional[list]

    # ── Tagged-questions batched turns (set once at start_interview) ─────────
    tagged_turns: Optional[list]        # list of pre-batched question strings
    tagged_turn_metas: Optional[list]   # parallel list of {type, options, question_id} dicts
    tagged_turn_index: int              # next turn to use (incremented by generate_question)
    tagged_form_template: Optional[dict]  # scale → {question: ""}
    tagged_question_meta: Optional[dict]  # scratch: meta for the current turn's question

    # ── Output ─────────────────────────────────────────────────
    response_text: str
    request_attachment: bool   # True when the frontend should show the upload button


def get_fresh_interview_state(user_id: str = "", form_id: str = "", session_id: str = "") -> InterviewState:
    """Return a blank InterviewState for a new interview. Replaces HealthAgent.init_form()."""
    fresh_form = _load_form("FRM-01").empty_form()
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
        referral_asked=False,
        visit_context="unknown",
        history=[{"role": "agent", "message": WELCOME_PROMPT}],
        missing_fields=[],
        summary_intent=None,
        reports_intent=None,
        is_correction_turn=False,
        correction_data=None,
        orchestrator_question_id=None,
        orchestrator_question_text=None,
        pending_question=None,
        mcp_questions=None,
        tagged_turns=None,
        tagged_turn_metas=None,
        tagged_turn_index=0,
        tagged_form_template=None,
        tagged_question_meta=None,
        response_text="",
        request_attachment=False,
    )
