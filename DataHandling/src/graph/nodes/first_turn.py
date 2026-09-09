"""
Node factory for handling the very first user response (phase == "welcome").

Uses the LLM as a conductor: reads the patient's opening message, reasons about
their visit intent, and generates the appropriate first question — no keyword lists.
"""
import json
from app.observability.privacy import error_type
from src.graph.state import InterviewState
from src.prompts import FIRST_TURN_CONDUCTOR_PROMPT

# Simple affirmative words that don't carry health information.
# For these, skip the full conductor and use a direct open question.
_CONFIRMATIONS = {
    "yes", "ok", "okay", "sure", "ready", "yep", "yeah", "yup", "alright",
    "go ahead", "start", "let's go", "lets go", "yeah can we start",
    "can we start", "yes we can", "i'm ready", "im ready", "let's start",
    "lets start", "begin", "yeah sure", "ok let's go", "ok lets go",
}


def make_handle_first_turn_node(llm_complete, welcome_prompt, reasoning_llm=None):
    def handle_first_turn_node(state: InterviewState) -> dict:
        user_input = state["user_input"]
        history = list(state["history"])

        visit_context = "unknown"
        response = None
        thinking = ""

        # Fast path: simple confirmation ("yeah can we start?") → skip full conductor
        _normalized = user_input.strip().lower().rstrip("!?.,")
        if _normalized in _CONFIRMATIONS or len(user_input.strip()) < 20:
            response = "I'm glad you're here! To help your clinician prepare, could you tell me what brings you in today and what you're hoping to address?"
            history.append({"role": "agent", "message": response})
            return {
                "response_text": response,
                "phase": "interviewing",
                "visit_context": "unknown",
                "history": history,
            }

        try:
            prompt = FIRST_TURN_CONDUCTOR_PROMPT.format(
                user_input=user_input.replace('"', '\\"')
            )
            raw = llm_complete(prompt).strip()

            # Parse THINKING: / VISIT_CONTEXT: / QUESTION: format
            # QUESTION can be multi-line (bullets) — capture everything after it
            _lines = raw.splitlines()
            _question_start = -1
            for _i, _line in enumerate(_lines):
                if _line.startswith("THINKING:"):
                    print("[first_turn] Model supplied internal reasoning metadata")
                elif _line.startswith("VISIT_CONTEXT:"):
                    vc = _line[14:].strip().lower().rstrip(".")
                    if vc in ("specific_complaint", "general_assessment", "clinic_inquiry", "unknown"):
                        visit_context = vc
                elif _line.startswith("QUESTION:") or _line.startswith("RESPONSE:"):
                    _question_start = _i
                    _prefix_len = 9  # len("QUESTION:") == len("RESPONSE:")
                    response = _line[_prefix_len:].strip()
            # Capture multi-line continuation after QUESTION:
            if _question_start >= 0:
                _extra = []
                for _line in _lines[_question_start + 1:]:
                    if _line.strip().startswith(("THINKING:", "VISIT_CONTEXT:")):
                        break
                    _extra.append(_line.strip())
                _extra_text = "\n".join(_extra).strip()
                if _extra_text:
                    response = (response + "\n" + _extra_text).strip()

            print(f"[first_turn] visit_context={visit_context}, response_chars={len(response or '')}")
        except Exception as e:
            print(f"[first_turn] Conductor failed: {error_type(e)}")

        # Fallback: if LLM failed or returned empty, generate a simple open question
        # Don't use the hardcoded Q1 — it asks about pain/complaints even for general visits
        if not response:
            # Check if message contains complaint keywords for a targeted fallback
            _lower_input = user_input.lower()
            _complaint_kw = ["pain", "hurt", "ache", "tight", "stiff", "injury", "issue", "problem"]
            if any(kw in _lower_input for kw in _complaint_kw):
                visit_context = "specific_complaint"
                response = (
                    "Thanks for sharing that. To make sure I capture everything accurately, "
                    "could you tell me more — specifically where you feel it, how severe it is "
                    "on a scale of 0 to 10, how long you've had it, and what makes it worse or better?"
                )
            else:
                response = (
                    "Thanks for reaching out! To help your clinician prepare, "
                    "could you tell me what brings you in today and what you're hoping to get from your visit?"
                )

        history.append({"role": "agent", "message": response})

        # Always include the current form in the result so LangGraph state is never
        # reverted to the empty initial form if no node updates it this turn.
        result = {
            "response_text": response,
            "phase": "interviewing",
            "visit_context": visit_context,
            "history": history,
            "form": state.get("form", {}),  # preserve existing form, may be overwritten below
        }

        # ── Extract form data from comprehensive first messages ───────────────
        # If the patient gave detailed health info in their first message (>80 chars),
        # run the reasoning extractor now so the form is filled immediately.
        # Without this, extraction only runs on the NEXT turn, losing the first message's data.
        if len(user_input.strip()) > 80 and reasoning_llm is not None:
            try:
                from src.graph.pure_functions.reasoning_extractor import reasoning_extract
                filled_form = reasoning_extract(
                    user_input=user_input,
                    history=history,
                    form=state.get("form", {}),
                    reasoning_llm=reasoning_llm,
                )
                if filled_form is not None:
                    result["form"] = filled_form
                    _count = sum(1 for sec in filled_form.values() if isinstance(sec, dict)
                                 for v in sec.values() if v and str(v).strip())
                    print(f"[first_turn] Reasoning extraction filled {_count} fields from first message")
            except Exception as _e:
                print(f"[first_turn] Reasoning extraction failed: {error_type(_e)}")

        # For general visits, immediately fill the irrelevant sections so they
        # never appear as missing to the conductor and the progress bar is accurate.
        if visit_context == "general_assessment":
            import copy as _copy
            form = _copy.deepcopy(state.get("form", {}))
            _na_fills = {
                "Present Complaint": {
                    "Primary Complaint": "General visit — no specific complaint",
                    "Duration of the Issue": "Not applicable",
                    "Onset (Gradual or Sudden)": "Not applicable",
                    "Mechanism of Injury or Cause": "Not applicable",
                },
                "Previous Consultations": {
                    "Previous Diagnosis or Advice and Prescribed Treatment Taken":
                        "Not applicable — general visit",
                    "Current Status of Issue (Improved, Same, Worse)": "Not applicable",
                },
                "Pain Assessment": {
                    "Primary Location of Pain": "Not applicable — no pain reported",
                    "Severity (1-10)": "Not applicable",
                    "Aggravating Factors": "Not applicable",
                    "Relieving Factors": "Not applicable",
                },
            }
            for section, fields in _na_fills.items():
                if section in form:
                    for field, value in fields.items():
                        existing = str(form[section].get(field, "")).strip().lower()
                        if not existing or existing in {"none", "not applicable", "n/a", ""}:
                            form[section][field] = value
            result["form"] = form

        return result

    return handle_first_turn_node
