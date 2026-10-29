"""Node factories for generating clinical intake questions and the final summary."""
import re

from langgraph.config import get_stream_writer
from src.graph.state import InterviewState
from src.graph.pure_functions.additional_complaint import normalize_additional_complaints
from src.graph.pure_functions.answer_mapping import (
    NOT_PROVIDED,
    complaint_labels,
    field_still_open,
    record_not_provided,
)
from src.graph.pure_functions.completion import completion_status
from src.graph.pure_functions.form_validation import first_section_batch, missing_in_ask_order
from src.graph.pure_functions.phrasing import pick, tone_lead, your
from src.graph.pure_functions.question_plan import (
    END_CONFIRM,
    END_CONTINUE,
    END_NOT_YET_MORE,
    END_NOT_YET_REQUIRED,
    END_PERCENT_NOTE,
    MANDATORY_LATER,
    MANDATORY_NOTICE,
    QUESTIONS_PER_TURN,
    RETRY_MANDATORY_NOTICE,
    question_for_missing_fields,
)
from src.graph.pure_functions.summary import _fallback_summary
from src.forms.loader import load_form as _load_form

# Load field labels and required flags once from FRM-01.md
_FRM01 = _load_form("FRM-01")
_FIELD_LABELS = _FRM01.field_labels

# A question is shown at most this many times, mandatory or not. It is then closed
# as Not provided and the interview moves on; for a mandatory question the patient
# is told to raise it with their clinician.
MAX_TIMES_ASKED = 3

# Per-turn signals that must not leak into the next turn.
_TURN_SIGNALS_RESET = {
    "noted_complaints": [],
    "decline_notice": False,
    "mandatory_notice": False,
    "end_requested": False,
    "section_skipped": False,
    "end_reply": "",
    "revised_answers": [],
    "saved_answers": [],
    "saved_field_ids": [],
    "patient_tone": "neutral",
}


def _complaint_section(section: str) -> str | None:
    """The Additional Complaint section a field belongs to, or None for the main intake."""
    return section if section.startswith("Additional Complaint ") else None


def _field_id(item: tuple[str, str]) -> str:
    return f"{item[0]}.{item[1]}"


def _active_complaint(form: dict, section: str) -> str:
    """The main complaint being asked about, as "your ...", or "" when there is only one problem.

    Only the sections about a single problem are named. Patient-level sections such
    as history and goals, and an added complaint's own section, are not.
    """
    labels = complaint_labels(form)
    if section not in labels or _complaint_section(section):
        return ""
    return your(labels[section])


def _worth_acknowledging(form: dict, history: list, saved_ids: list[str]) -> bool:
    """True when what the patient just saved is worth saying out loud.

    In the normal flow every question asked is answered and the bot simply moves on.
    It speaks up only when part of the last batch is still unanswered, or when the
    patient volunteered something nobody asked.
    """
    asked = next(
        (entry["question_field_ids"] for entry in reversed(history)
         if entry.get("role") == "agent" and entry.get("question_field_ids")),
        [],
    )
    if not asked or not saved_ids:
        return False
    return any(field_still_open(form, field_id) for field_id in asked) or any(
        field_id not in asked for field_id in saved_ids
    )


def _section_intro(history: list, batch: list[tuple[str, str]]) -> str:
    """The form's opening sentence for the batch's section, when the section is new.

    A batch starts a new section when the questions last put to the patient were
    in another section, or when no question has been asked yet (the opening
    message is not a question). Follow-ups in the same section get none.
    """
    section = batch[0][0]
    for entry in reversed(history):
        if entry.get("role") == "agent" and entry.get("question_field_ids"):
            previous = {field_id.split(".", 1)[0] for field_id in entry["question_field_ids"]}
            if section in previous:
                return ""
            break
    return _FRM01.section_intro(section)


def _plain_label(field: str) -> str:
    """A field name as a patient would read it, without the scale hint."""
    return re.sub(r"\s*\([^)]*\)", "", field).strip()


def _saved_note(fields: list[str], turn: int, following: str) -> str:
    """Name what the patient's reply just gave us, or "" when nothing can be said naturally.

    Only fields the form has a plain-words phrase for are named. Nothing is said
    for the rest, so the bot never falls back on a vague line that says nothing.
    """
    phrases = list(dict.fromkeys(
        phrase for phrase in (_FRM01.ack_phrase(field) for field in fields) if phrase
    ))[:3]
    if not phrases:
        return ""
    things = phrases[0] if len(phrases) == 1 else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    return pick("saved", turn, avoid=following).format(things=things)


def _other_complaint_note(form: dict, saved_ids: list[str], active_section: str, turn: int) -> str:
    """Acknowledge details given about another complaint, and say the main one comes first.

    The details are already saved. This only tells the patient so, and that the
    questions about the main complaint come first, so they know which problem
    each question is for. Said only while a main-complaint section is being asked.
    """
    active = _active_complaint(form, active_section)
    if not active:
        return ""
    labels = complaint_labels(form)
    fields_by_section: dict[str, list[str]] = {}
    for field_id in saved_ids:
        section, name = field_id.split(".", 1)
        if section.startswith("Additional Complaint ") and section in labels:
            fields_by_section.setdefault(section, []).append(name)
    if not fields_by_section:
        return ""
    section, names = next(iter(fields_by_section.items()))
    phrases = list(dict.fromkeys(p for p in (_FRM01.ack_phrase(n) for n in names) if p))[:3]
    things = (
        "what you told me" if not phrases
        else phrases[0] if len(phrases) == 1
        else ", ".join(phrases[:-1]) + " and " + phrases[-1]
    )
    return pick("other_complaint_saved", turn).format(
        things=things, complaint=your(labels[section]), active=active
    )


def _revision_note(revised: list[dict], has_questions: bool, turn: int, following: str = "") -> str:
    """Say which earlier answers the patient just changed, and return to the open questions."""
    changes = "; ".join(f"{_plain_label(item['field'])} to {item['value']}" for item in revised)
    note = pick("revision", turn, avoid=following).format(changes=changes)
    if has_questions:
        note += " " + pick("return_to_questions", turn)
    return note


def _with_turn_notes(
    response: str,
    state: InterviewState,
    uncaptured: list[str] = (),
    *,
    has_questions: bool = False,
    turn: int = 0,
    other_note: str = "",
    acknowledge_saved: bool = False,
) -> str:
    """Prefix what the patient should hear about their last message."""
    revised = state.get("revised_answers") or []
    noted = [label for label in (state.get("noted_complaints") or []) if str(label).strip()]
    if revised:
        response = f"{_revision_note(revised, has_questions, turn, response)}\n\n{response}"
    elif other_note and not noted and not state.get("decline_notice"):
        response = f"{other_note}\n\n{response}"
    elif acknowledge_saved and not noted and not state.get("decline_notice"):
        # One acknowledgement per turn: a noted complaint, a skip or an update already
        # answers the patient, so what was saved is only named when none of those did.
        saved = _saved_note(state.get("saved_answers") or [], turn, response)
        if saved:
            response = f"{saved}\n\n{response}"
    if uncaptured:
        labels = ", ".join(_plain_label(field) for field in uncaptured)
        response = f"{MANDATORY_LATER.format(labels=labels)}\n\n{response}"
    if state.get("decline_notice"):
        key = "section_skipped" if state.get("section_skipped") else "skip_accepted"
        response = f"{pick(key, turn, avoid=response)}\n\n{response}"
    if noted:
        response = f"{pick('noted', turn, avoid=response).format(labels=', '.join(noted))}\n\n{response}"
    return response


def _reply_acknowledged(state: InterviewState, uncaptured: list[str]) -> bool:
    """True when the patient's last message already got a reply of its own.

    A new complaint noted, a skip accepted, or a mandatory notice shown all mean
    the message was understood, so the questions that follow must not apologise
    for not catching it.
    """
    noted = any(str(label).strip() for label in (state.get("noted_complaints") or []))
    return bool(
        noted
        or state.get("revised_answers")
        or state.get("saved_answers")
        or state.get("decline_notice")
        or state.get("mandatory_notice")
        or state.get("end_reply") == "declined"
        or uncaptured
    )


def _end_notice(mandatory_outstanding: bool, current: float, minimum: float) -> str:
    """Why a patient who asked to end cannot yet, and what it takes to finish."""
    shown = {"current": f"{current:.0f}", "minimum": f"{minimum:.0f}"}
    if mandatory_outstanding:
        note = END_NOT_YET_REQUIRED
        if current < minimum:
            note = f"{note} {END_PERCENT_NOTE.format(**shown)}"
        return note
    return END_NOT_YET_MORE.format(**shown)


def _lead_notice(
    state: InterviewState,
    batch: list[tuple[str, str]],
    ask_counts: dict,
    *,
    end_notice: str,
    acknowledged: bool,
    turn: int = 0,
) -> str:
    """The one sentence that opens a batch that is a goodbye, a refusal, or a repeat.

    A goodbye comes first: the patient is telling us they want to leave, whatever
    else the message did. The repeat apology is only for a reply that registered
    as nothing, so it is left out when ``acknowledged``.
    """
    if end_notice:
        return end_notice
    if state.get("end_reply") == "declined":
        return END_CONTINUE
    has_mandatory = any(_FRM01.is_mandatory(section, field) for section, field in batch)
    if state.get("mandatory_notice") and has_mandatory:
        return MANDATORY_NOTICE
    if not acknowledged and all(ask_counts.get(_field_id(item), 0) >= 1 for item in batch):
        return RETRY_MANDATORY_NOTICE if has_mandatory else pick("retry", turn)
    return ""


def next_intake_step(state: InterviewState, history: list) -> dict:
    """Return the patch for the next turn: a question batch, or the summary.

    Questions are asked one section at a time, and only the fields still open in
    that section. Sections that still need a mandatory answer come first. A
    question is shown at most MAX_TIMES_ASKED times, then closed as Not provided
    and the interview moves on. When that was a mandatory question the patient is
    told to raise it with their clinician. A patient who asks to end is held to the
    mandatory questions that can still be asked, then let go.
    """

    form = state["form"]
    turn = len(history)
    visit_context = state.get("visit_context", "unknown")
    ask_counts = dict(state.get("question_ask_counts") or {})
    missing = []
    if not state.get("tagged_form_template"):
        missing = missing_in_ask_order(form, state["form_sections"], visit_context)

    exhausted = [item for item in missing if ask_counts.get(_field_id(item), 0) >= MAX_TIMES_ASKED]
    uncaptured = [field for section, field in exhausted if _FRM01.is_mandatory(section, field)]
    if exhausted:
        print(f"[generate] Closed {len(exhausted)} fields after {MAX_TIMES_ASKED} asks")
        form = record_not_provided(form, [_field_id(item) for item in exhausted], NOT_PROVIDED)
        missing = [item for item in missing if item not in exhausted]

    # A goodbye is honoured once no mandatory question can still be asked and
    # enough of the form is done; until then the interview carries on.
    ending = bool(state.get("end_requested")) and not state.get("tagged_form_template")
    end_notice = ""
    if ending:
        minimum = state.get("completion_percentage", 0)
        status = completion_status(form, state["form_sections"], visit_context, minimum)
        if status.can_finish and state.get("end_reply") != "confirmed":
            # Nothing stops the patient ending, so confirm it once before doing it.
            response = _with_turn_notes(END_CONFIRM, state, uncaptured)
            return {
                "form": form,
                "response_text": response,
                "history": history + [{"role": "agent", "message": response, "end_confirm": True}],
                "phase": "interviewing",
                "current_section": state.get("current_section", ""),
                "missing_fields": [field for _, field in missing],
                "question_field_ids": [],
                "question_ask_counts": ask_counts,
                **_TURN_SIGNALS_RESET,
            }
        if status.can_finish:
            form = record_not_provided(form, [_field_id(item) for item in missing], NOT_PROVIDED)
            missing = []
        else:
            mandatory_outstanding = bool(status.mandatory_missing)
            end_notice = _end_notice(mandatory_outstanding, status.percent, minimum)
            if mandatory_outstanding:
                missing = [item for item in missing if _FRM01.is_mandatory(*item)]

    if missing:
        batch = first_section_batch(missing, QUESTIONS_PER_TURN)
        question_ids = [_field_id(item) for item in batch]
        lead = _lead_notice(
            state, batch, ask_counts,
            end_notice=end_notice,
            acknowledged=_reply_acknowledged(state, uncaptured),
            turn=turn,
        )
        for question_id in question_ids:
            ask_counts[question_id] = ask_counts.get(question_id, 0) + 1
        active = _active_complaint(form, batch[0][0])
        other_note = _other_complaint_note(form, state.get("saved_field_ids") or [], batch[0][0], turn)
        intro = _section_intro(history, batch)
        question_text = question_for_missing_fields(
            batch, _FIELD_LABELS, max_fields=QUESTIONS_PER_TURN, about=active
        )
        response = question_for_missing_fields(
            batch, _FIELD_LABELS, max_fields=QUESTIONS_PER_TURN, intro=intro, about=active
        )
        additional = _complaint_section(batch[0][0])
        if additional:
            label = str(form[additional].get("Primary Complaint") or "").strip()
            response = f"Now about your other concern, {label}.\n\n{response}"
        if lead:
            response = f"{lead}\n\n{response}"
        response = _with_turn_notes(
            response, state, uncaptured, has_questions=True, turn=turn, other_note=other_note,
            acknowledge_saved=_worth_acknowledging(form, history, state.get("saved_field_ids") or []),
        )
        empathy = tone_lead(state.get("patient_tone"), turn, avoid=response)
        if empathy:
            response = f"{empathy}\n\n{response}"
        return {
            "form": form,
            "response_text": response,
            "history": history + [{
                "role": "agent",
                "message": response,
                "question_field_ids": question_ids,
                "question_text": question_text,
            }],
            "phase": "interviewing",
            "current_section": batch[0][0],
            "missing_fields": [field for _, field in missing],
            "question_field_ids": question_ids,
            "question_ask_counts": ask_counts,
            **_TURN_SIGNALS_RESET,
        }

    summary_form = normalize_additional_complaints(form)
    summary = _with_turn_notes(_fallback_summary(summary_form), state, uncaptured, turn=turn)
    return {
        "form": summary_form,
        "response_text": summary,
        "history": history + [{"role": "agent", "message": summary}],
        "phase": "summary",
        "question_round": 3,
        "missing_fields": [],
        "question_field_ids": [],
        "question_ask_counts": {},
        **_TURN_SIGNALS_RESET,
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
