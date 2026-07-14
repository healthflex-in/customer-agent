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
from langgraph.config import get_stream_writer
from src.graph.state import InterviewState
from src.graph.pure_functions.summary import generate_interview_summary
from src.graph.pure_functions.form_validation import get_all_missing_fields, validate_section as _vs
from src.prompts import INTELLIGENT_QUESTION_PROMPT


# ── Visit context descriptions injected into conductor prompt ─────────────────
_VISIT_CONTEXT_DESCRIPTIONS = {
    "specific_complaint": (
        "SPECIFIC COMPLAINT — patient has a specific pain, injury, or condition to address. "
        "Collect: complaint details, pain severity/location, duration, onset, aggravating/relieving "
        "factors, previous consultations, health history, diagnostics, treatment goals."
    ),
    "general_assessment": (
        "GENERAL ASSESSMENT / WELLNESS VISIT — patient has NO specific complaint. "
        "They want a check-up, posture review, or to explore the clinic. "
        "SKIP all questions about specific pain, injury mechanism, and previous treatment for a complaint. "
        "Collect ONLY: general health conditions, lifestyle, treatment/wellness goals, referral source."
    ),
    "clinic_inquiry": (
        "CLINIC INQUIRY — patient was asking about the clinic. They may or may not have a complaint. "
        "Collect whatever health context they're willing to share: any conditions, lifestyle, goals."
    ),
    "unknown": (
        "VISIT TYPE UNKNOWN — gather what you can. "
        "Start with what brings them in today, then collect health context naturally."
    ),
}

# Sections skipped entirely for general assessment visits
_SKIP_FOR_GENERAL = {"Pain Assessment", "Previous Consultations", "Present Complaint"}

# Human-readable field labels for the "needed" summary
_FIELD_LABELS = {
    "Primary Complaint": "What's bothering them (pain/issue, location, severity 0–10)",
    "Duration of the Issue": "How long they've had this",
    "Onset (Gradual or Sudden)": "Whether it started suddenly or gradually",
    "Mechanism of Injury or Cause": "What caused it / how it started",
    "Previous Diagnosis or Advice and Prescribed Treatment Taken": "Previous doctor/physio visits and what they said",
    "Current Status of Issue (Improved, Same, Worse)": "Whether the condition has improved, stayed same, or worsened",
    "Primary Location of Pain": "Exactly where the pain is",
    "Severity (1-10)": "Pain severity on a scale of 0–10",
    "Aggravating Factors": "What makes it worse",
    "Relieving Factors": "What gives relief",
    "Systemic Illness and Surgical History": "Other health conditions, past surgeries or fractures",
    "Current Lifestyle": "Smoking/drinking habits, exercise, job type",
    "Reports": "Any MRI, X-ray, CT scan, or blood reports",
    "Short-Term Goals (within 3 months)": "What they want to achieve in the next 3 months",
    "Long-Term Goals (after 3 months)": "Their long-term health/activity goal",
    "Specific Expectations from Treatment": "What they expect from this treatment",
}


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
    prompt = INTELLIGENT_QUESTION_PROMPT.format(
        visit_context_description=_VISIT_CONTEXT_DESCRIPTIONS.get(
            visit_context, _VISIT_CONTEXT_DESCRIPTIONS["unknown"]
        ),
        history_text=_build_history_text(history),
    )
    raw = llm_complete(prompt).strip()

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
        print(f"[generate] format fallback: {content_lines[0][:60]}")
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


def make_generate_question_node(llm_complete, system_prompt, predefined_questions):
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

        # ── Collect relevant missing fields ───────────────────────────────────
        flat_missing = _get_relevant_missing(form, form_sections, visit_context)

        if flat_missing:
            response = _generate_intelligent_question(flat_missing, history, visit_context, form, llm_complete)

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
