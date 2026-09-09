"""
Node factories for generating questions and the final summary.

Key behaviours:
- The LLM acts as a conductor: it sees full conversation context, the current form
  state, and the visit type — then decides the single best next question to ask.
- Visit context (specific_complaint / general_assessment / clinic_inquiry) drives
  which sections are relevant; the LLM skips irrelevant questions naturally.
- The referral question ("How did you hear about us?") is asked exactly once, at
  the end, by code — not left to the LLM to forget or repeat.
"""
import time as _time
from langgraph.config import get_stream_writer
from src.graph.state import InterviewState
from src.graph.pure_functions.summary import generate_interview_summary
from src.graph.pure_functions.form_validation import get_all_missing_fields, validate_section as _vs
from src.prompts import INTELLIGENT_QUESTION_PROMPT
from src.forms.loader import load_form as _load_form

# Load visit-type rules and field labels once from FRM-01.md
_FRM01 = _load_form("FRM-01")
_VISIT_CONTEXT_DESCRIPTIONS = {k: v.description for k, v in _FRM01.visit_types.items()}
_SKIP_FOR_GENERAL = _FRM01.visit_types["general_assessment"].skip_sections
_FIELD_LABELS = _FRM01.field_labels


def _get_relevant_missing(form: dict, form_sections: list, visit_context: str) -> list[tuple]:
    """Return [(section, field), ...] for missing fields relevant to this visit type."""
    all_missing = get_all_missing_fields(form, form_sections)
    all_missing.pop("Referral", None)  # handled separately

    if visit_context == "general_assessment":
        for sec in _SKIP_FOR_GENERAL:
            all_missing.pop(sec, None)

    return [
        (sec, field)
        for sec, fields in all_missing.items()
        for field in fields
        if "(If Any)" not in field and "(Optional)" not in field
    ]


def _build_history_text(history: list) -> str:
    lines = []
    for entry in history:
        role = "Sage" if entry.get("role") == "agent" else "Patient"
        lines.append(f"{role}: {entry.get('message', '').strip()}")
    return "\n".join(lines) if lines else "(no conversation yet)"


def _build_collected_summary(form: dict) -> str:
    lines = []
    for section, fields in form.items():
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            if value and str(value).strip():
                lines.append(f"  • {field}: {value}")
    return "\n".join(lines) if lines else "Nothing collected yet."


def _build_needed_summary(flat_missing: list[tuple]) -> str:
    if not flat_missing:
        return "Nothing — all required fields are filled."
    lines = []
    for section, field in flat_missing:
        label = _FIELD_LABELS.get(field, field)
        lines.append(f"  • [{section}] {label}")
    return "\n".join(lines)


def _generate_intelligent_question(
    flat_missing: list[tuple],
    history: list,
    visit_context: str,
    form: dict,
    llm_complete,
    mcp_hints: list | None = None,
) -> str:
    """
    LLM conductor: reads the full conversation and decides the single most
    appropriate next question based on what the patient has already covered.
    Does NOT depend on form state — conversation is the source of truth.
    Returns the question text only (thinking is logged, never shown to user).
    """
    import json as _json

    # Pass only conversation + visit type — no form state.
    # The conductor reads the conversation directly to determine coverage,
    # which is more reliable than depending on extraction having worked.
    _mcp_section = ""
    if mcp_hints:
        _top = mcp_hints[:5]
        _lines = "\n".join(f"  - {r.get('text', r.get('category', ''))}" for r in _top)
        _mcp_section = f"\n\nClinically recommended topics for this case (from intake question bank):\n{_lines}\nPrioritise these if not yet covered."

    prompt = INTELLIGENT_QUESTION_PROMPT.format(
        visit_context_description=_VISIT_CONTEXT_DESCRIPTIONS.get(
            visit_context, _VISIT_CONTEXT_DESCRIPTIONS["unknown"]
        ),
        history_text=_build_history_text(history),
    ) + _mcp_section
    _t0 = _time.perf_counter()
    raw = llm_complete(prompt).strip()
    print(f"[timing] generate_question_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms")

    # Parse THINKING: / QUESTION: format
    # QUESTION can be multi-line (bullets), so capture everything after "QUESTION:" to end
    thinking = ""
    question = ""
    question_start = -1

    lines = raw.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("THINKING:"):
            thinking = line[len("THINKING:"):].strip()
        elif line.startswith("QUESTION:"):
            question_start = i
            question = line[len("QUESTION:"):].strip()

    # Capture all lines after QUESTION: (multi-line bullets etc.)
    if question_start >= 0:
        extra = []
        for line in lines[question_start + 1:]:
            stripped = line.strip()
            if stripped.startswith(("THINKING:", "VISIT_CONTEXT:")):
                break
            extra.append(stripped)
        if extra:
            # Join with newlines to preserve bullet structure
            extra_text = "\n".join(extra).strip()
            if extra_text:
                question = (question + "\n" + extra_text).strip()

    if thinking:
        print(f"[generate] thinking: {thinking}")
    if question:
        return question

    # Fallback: return raw output if format not followed, skipping format header lines
    content_lines = [l.strip() for l in lines
                     if l.strip() and not l.strip().startswith(("THINKING:", "QUESTION:", "VISIT_CONTEXT:"))]
    if content_lines:
        print("[generate] Applied output-format fallback")
        return "\n".join(content_lines)
    return raw


def _fill_unanswered_fields(form: dict) -> dict:
    """
    Before generating the summary, fill any remaining empty fields with
    'Not mentioned by patient' so no field in the final document is blank.
    Skips fields already filled and fields marked as Not applicable.
    """
    import copy
    filled = copy.deepcopy(form)
    for section, fields in filled.items():
        if not isinstance(fields, dict):
            continue
        for field, value in fields.items():
            if not value or not str(value).strip():
                filled[section][field] = "Not mentioned by patient"
    return filled


def make_generate_question_node(llm_complete, system_prompt):
    def _make_summary(state, history):
        # Fill any remaining empty fields before generating the summary
        complete_form = _fill_unanswered_fields(state["form"])
        summary = generate_interview_summary(complete_form, history, llm_complete)
        history.append({"role": "agent", "message": summary})
        return {
            "response_text": summary,
            "form": complete_form,   # persist the filled form to state
            "phase": "summary",
            "history": history,
            "question_round": 3,
        }

    def generate_question_node(state: InterviewState) -> dict:
        writer = get_stream_writer()
        writer({"stage": "Formulating next question", "detail": "reasoning about context", "status": "active"})

        form = state["form"]
        form_sections = state["form_sections"]
        question_round = state["question_round"]
        history = list(state["history"])
        referral_asked = state.get("referral_asked", False)
        visit_context = state.get("visit_context", "unknown")

        # ── Tagged turns: use pre-batched questions when available ────────────
        tagged_turns = state.get("tagged_turns") or []
        tagged_turn_metas = state.get("tagged_turn_metas") or []
        tagged_turn_index = state.get("tagged_turn_index", 0)
        if tagged_turns:
            if tagged_turn_index < len(tagged_turns):
                response = tagged_turns[tagged_turn_index]
                meta = tagged_turn_metas[tagged_turn_index] if tagged_turn_index < len(tagged_turn_metas) else None
                history.append({"role": "agent", "message": response})
                writer({"stage": "Formulating next question", "detail": "Ready", "status": "done"})
                return {
                    "response_text": response,
                    "history": history,
                    "tagged_turn_index": tagged_turn_index + 1,
                    "tagged_question_meta": meta,
                }
            else:
                # All pre-batched turns done — go straight to summary
                writer({"stage": "Formulating next question", "detail": "Generating summary", "status": "active"})
                return _make_summary(state, history)

        # ── Collect relevant missing fields ───────────────────────────────────
        flat_missing = _get_relevant_missing(form, form_sections, visit_context)

        if flat_missing:
            # Use the pre-computed question from the extract node (ran concurrently)
            # if available, otherwise fall back to a fresh LLM call.
            pending_q = state.get("pending_question")
            mcp_questions = state.get("mcp_questions") or []
            if pending_q:
                response = pending_q
                print(f"[generate] using prefetched question (saved concurrent LLM call)")
            else:
                response = _generate_intelligent_question(
                    flat_missing, history, visit_context, form, llm_complete,
                    mcp_hints=mcp_questions,
                )

            # LLM signals "DONE" when it thinks everything is collected
            if response.strip().upper() == "DONE":
                writer({"stage": "Formulating next question", "detail": "Interview complete", "status": "done"})
                if not referral_asked:
                    response = (
                        "Last thing — how did you come to know about us? "
                        "(Friend/family, Google, Instagram, Facebook, YouTube, doctor referral, etc.)"
                    )
                    history.append({"role": "agent", "message": response})
                    return {"response_text": response, "history": history, "referral_asked": True}
                return _make_summary(state, history)

            history.append({"role": "agent", "message": response})
            writer({"stage": "Formulating next question", "detail": "Ready", "status": "done"})
            return {"response_text": response, "history": history}

        # ── All non-referral fields done — ask referral exactly once ──────────
        # Only ask if Source isn't already filled (e.g. patient mentioned it earlier)
        referral_source_filled = bool(
            form.get("Referral", {}).get("Source", "").strip()
        )
        if not referral_asked and not referral_source_filled:
            response = (
                "Last thing — how did you come to know about us? "
                "(Friend/family, Google, Instagram, Facebook, YouTube, doctor referral, etc.)"
            )
            history.append({"role": "agent", "message": response})
            writer({"stage": "Formulating next question", "detail": "Ready", "status": "done"})
            return {
                "response_text": response,
                "history": history,
                "referral_asked": True,
            }
        elif referral_source_filled and not referral_asked:
            # Referral already captured — mark as asked so we skip to summary
            return {"referral_asked": True}

        # ── All fields done including referral — generate summary ─────────────
        writer({"stage": "Formulating next question", "detail": "Generating summary", "status": "active"})
        return _make_summary(state, history)

    return generate_question_node


def make_generate_summary_node(llm_complete):
    def generate_summary_node(state: InterviewState) -> dict:
        history = list(state["history"])
        complete_form = _fill_unanswered_fields(state["form"])
        summary = generate_interview_summary(complete_form, history, llm_complete)
        history.append({"role": "agent", "message": summary})
        return {
            "response_text": summary,
            "form": complete_form,
            "phase": "summary",
            "history": history,
        }

    return generate_summary_node
