"""Node factories for generating clinical intake questions and the final summary."""
from langgraph.config import get_stream_writer
from src.graph.state import InterviewState
from src.graph.pure_functions.additional_complaint import normalize_additional_complaints
from src.graph.pure_functions.answer_mapping import record_not_provided
from src.graph.pure_functions.form_validation import get_all_missing_fields
from src.graph.pure_functions.question_plan import (
    CLINICIAN_LATER,
    NOTED_COMPLAINT,
    REFRAME_LEAD,
    question_for_missing_fields,
)
from src.graph.pure_functions.summary import _fallback_summary
from src.forms.loader import load_form as _load_form

# Load visit-type rules and field labels once from FRM-01.md
_FRM01 = _load_form("FRM-01")
_SKIP_FOR_GENERAL = _FRM01.visit_types["general_assessment"].skip_sections
_FIELD_LABELS = _FRM01.field_labels

# A question is shown at most this many times before it is saved as Not provided.
MAX_TIMES_ASKED = 3
QUESTIONS_PER_TURN = 4


def _get_relevant_missing(form: dict, form_sections: list, visit_context: str) -> list[tuple]:
    """Return [(section, field), ...] for missing fields relevant to this visit type."""
    all_missing = get_all_missing_fields(form, form_sections)

    if visit_context == "general_assessment":
        for sec in _SKIP_FOR_GENERAL:
            all_missing.pop(sec, None)

    return [
        (sec, field)
        for sec, fields in all_missing.items()
        for field in fields
        if "(If Any)" not in field and "(Optional)" not in field
    ]


def _complaint_section(section: str) -> str | None:
    """The Additional Complaint section a field belongs to, or None for the main intake."""
    return section if section.startswith("Additional Complaint ") else None


def next_intake_step(state: InterviewState, history: list) -> dict:
    """Return the patch for the next turn: a question batch or the summary.

    Required fields are asked in FRM-01 order, a few per turn. A field still
    unanswered after MAX_TIMES_ASKED is saved as Not provided, so the interview
    always reaches the summary.
    """

    form = state["form"]
    ask_counts = dict(state.get("question_ask_counts") or {})
    reframe_ids = list(state.get("skip_reframe_ids") or [])
    reframed_ids = list(state.get("skip_reframed_ids") or [])
    missing = []
    if not state.get("tagged_form_template"):
        missing = _get_relevant_missing(
            form, state["form_sections"], state.get("visit_context", "unknown")
        )
    exhausted = [
        f"{section}.{field}" for section, field in missing
        if ask_counts.get(f"{section}.{field}", 0) >= MAX_TIMES_ASKED
        and f"{section}.{field}" not in reframe_ids
    ]
    if exhausted:
        print(f"[generate] Saved {len(exhausted)} unanswered fields as Not provided")
        form = record_not_provided(form, exhausted)
        missing = [
            (section, field) for section, field in missing
            if f"{section}.{field}" not in exhausted
        ]

    if missing:
        # One batch never mixes complaints, so the patient knows which one each question is about.
        batch = [
            item for item in missing[:QUESTIONS_PER_TURN]
            if _complaint_section(item[0]) == _complaint_section(missing[0][0])
        ]
        question_ids = [f"{section}.{field}" for section, field in batch]
        for question_id in question_ids:
            ask_counts[question_id] = ask_counts.get(question_id, 0) + 1
        shown_reframe = [field_id for field_id in question_ids if field_id in reframe_ids]
        reframe_ids = [field_id for field_id in reframe_ids if field_id not in shown_reframe]
        reframed_ids = list(dict.fromkeys([*reframed_ids, *shown_reframe]))
        response = question_for_missing_fields(
            batch,
            _FIELD_LABELS,
            max_fields=QUESTIONS_PER_TURN,
            reframe_fields={field_id.split(".", 1)[1] for field_id in shown_reframe},
        )
        additional = _complaint_section(batch[0][0])
        if additional:
            label = str(form[additional].get("Primary Complaint") or "").strip()
            response = f"Now about your other concern, {label}.\n\n{response}"
        if shown_reframe:
            response = f"{REFRAME_LEAD}\n\n{response}"
        if state.get("decline_notice"):
            response = f"{CLINICIAN_LATER}\n\n{response}"
        noted = [label for label in (state.get("noted_complaints") or []) if str(label).strip()]
        if noted:
            response = f"{NOTED_COMPLAINT.format(labels=', '.join(noted))}\n\n{response}"
        return {
            "form": form,
            "response_text": response,
            "history": history + [{
                "role": "agent",
                "message": response,
                "question_field_ids": question_ids,
            }],
            "phase": "interviewing",
            "current_section": batch[0][0],
            "missing_fields": [field for _, field in missing],
            "question_field_ids": question_ids,
            "question_ask_counts": ask_counts,
            "skip_reframe_ids": reframe_ids,
            "skip_reframed_ids": reframed_ids,
            "noted_complaints": [],
            "decline_notice": False,
        }

    summary_form = normalize_additional_complaints(form)
    summary = _fallback_summary(summary_form)
    if state.get("decline_notice"):
        summary = f"{CLINICIAN_LATER}\n\n{summary}"
    noted = [label for label in (state.get("noted_complaints") or []) if str(label).strip()]
    if noted:
        summary = f"{NOTED_COMPLAINT.format(labels=', '.join(noted))}\n\n{summary}"
    return {
        "form": summary_form,
        "response_text": summary,
        "history": history + [{"role": "agent", "message": summary}],
        "phase": "summary",
        "question_round": 3,
        "missing_fields": [],
        "question_field_ids": [],
        "question_ask_counts": {},
        "skip_reframe_ids": reframe_ids,#before sage repharses the questions
        "skip_reframed_ids": reframed_ids,
        "noted_complaints": [],
        "decline_notice": False,
    }


def make_generate_question_node(llm_complete, system_prompt):
    def generate_question_node(state: InterviewState) -> dict:
        writer = get_stream_writer()
        writer({"stage": "Formulating next question", "detail": "reasoning about context", "status": "active"})

        # ── Tagged turns: use pre-batched questions when available ────────────
        tagged_turns = state.get("tagged_turns") or []
        tagged_turn_metas = state.get("tagged_turn_metas") or []
        tagged_turn_index = state.get("tagged_turn_index", 0)
        if tagged_turns and tagged_turn_index < len(tagged_turns):
            response = tagged_turns[tagged_turn_index]
            meta = tagged_turn_metas[tagged_turn_index] if tagged_turn_index < len(tagged_turn_metas) else None
            writer({"stage": "Formulating next question", "detail": "Ready", "status": "done"})
            return {
                "response_text": response,
                "history": list(state["history"]) + [{"role": "agent", "message": response}],
                "tagged_turn_index": tagged_turn_index + 1,
                "tagged_question_meta": meta,
            }

        step = next_intake_step(state, list(state["history"]))
        writer({"stage": "Formulating next question", "detail": "Ready", "status": "done"})
        return step

    return generate_question_node


def make_generate_summary_node(llm_complete):
    def generate_summary_node(state: InterviewState) -> dict:
        return next_intake_step(state, list(state["history"]))

    return generate_summary_node
