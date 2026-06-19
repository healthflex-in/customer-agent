"""
Node factories for generating questions and the final summary.
"""
from src.graph.state import InterviewState
from src.graph.pure_functions.summary import generate_interview_summary
from src.graph.pure_functions.form_validation import validate_section as _vs


def make_generate_question_node(llm_complete, system_prompt, predefined_questions):
    def generate_question_node(state: InterviewState) -> dict:
        current_section = state["current_section"]
        question_round = state["question_round"]
        form = state["form"]
        history = list(state["history"])

        question_text = None

        if question_round < len(predefined_questions):
            raw_question = predefined_questions[question_round][1]

            if question_round == 0:
                # Check if the form is empty (all fields blank)
                all_empty = all(
                    not str(v).strip()
                    for section_fields in form.values()
                    if isinstance(section_fields, dict)
                    for v in section_fields.values()
                )
                if all_empty:
                    # Return the comprehensive first question verbatim — no LLM needed
                    question_text = "DIRECT_QUESTION:" + raw_question
                else:
                    # Form has partial data — generate a follow-up focused on the
                    # CURRENT SECTION's missing fields. Only fall back to global
                    # missing if the current section is complete.
                    section_missing = _vs(form, current_section)
                    # Filter out optional fields from the question
                    section_missing = [
                        f for f in section_missing
                        if "(If Any)" not in f and "(Optional)" not in f
                    ]

                    if section_missing:
                        missing_list = "\n".join(f"- {f}" for f in section_missing)
                        # Summarise what the patient has already shared so the LLM
                        # can acknowledge it briefly (once, not every turn).
                        filled_facts = [
                            f"{field}: {value}"
                            for sec_data in form.values()
                            if isinstance(sec_data, dict)
                            for field, value in sec_data.items()
                            if value and str(value).strip()
                        ]
                        already_shared = (
                            "Already collected: " + "; ".join(filled_facts[:5])
                            if filled_facts else "Nothing collected yet."
                        )
                        prompt = f"""You are a warm, friendly physiotherapy intake assistant having a natural conversation.

{already_shared}

Still needed from the patient ({current_section}):
{missing_list}

Write ONE short question to collect the missing information above.

STRICT RULES — breaking any of these is an error:
1. NEVER start with "Thank you for sharing", "Thank you for providing", "I appreciate", "I understand you", or any variation of these.
2. NEVER say "To help me understand" or "To ensure I have".
3. Use a different, natural opener every time — examples: "Got it!", "Noted.", "That's helpful.", "Okay,", "Right,", "Makes sense.", or just ask directly with no preamble.
4. Keep it to 1–2 sentences maximum.
5. Sound like a real person, not a form.
6. Output ONLY the question. No labels, no explanation."""
                        question_text = llm_complete(prompt)
                    else:
                        # Current section complete — use the predefined question verbatim
                        question_text = "DIRECT_QUESTION:" + raw_question

            else:
                # Rounds 1 and 2: ask about the CURRENT SECTION's missing fields
                # specifically, so we never re-ask something already answered.
                section_missing = _vs(form, current_section)
                section_missing = [
                    f for f in section_missing
                    if "(If Any)" not in f and "(Optional)" not in f
                ]
                if section_missing:
                    missing_list = "\n".join(f"- {f}" for f in section_missing)
                    filled_facts = [
                        f"{field}: {value}"
                        for sec_data in form.values()
                        if isinstance(sec_data, dict)
                        for field, value in sec_data.items()
                        if value and str(value).strip()
                    ]
                    already_shared = (
                        "Already collected: " + "; ".join(filled_facts[:6])
                        if filled_facts else "Nothing collected yet."
                    )
                    prompt = f"""You are a warm, friendly physiotherapy intake assistant having a natural conversation.

{already_shared}

Still needed from the patient ({current_section}):
{missing_list}

Write ONE short question to collect the missing information above.

STRICT RULES — breaking any of these is an error:
1. NEVER start with "Thank you for sharing", "Thank you for providing", "I appreciate", "I understand you", or any variation of these.
2. NEVER say "To help me understand" or "To ensure I have".
3. Use a different, natural opener every time — examples: "Got it!", "Noted.", "That's helpful.", "Okay,", "Right,", "Makes sense.", or just ask directly with no preamble.
4. Keep it to 1–2 sentences maximum.
5. Sound like a real person, not a form.
6. Output ONLY the question. No labels, no explanation."""
                    question_text = llm_complete(prompt)
                else:
                    # Current section already fully filled — use predefined verbatim
                    question_text = "DIRECT_QUESTION:" + raw_question

        else:
            # Ran out of predefined questions; generate a closing follow-up via LLM
            prompt = (
                "You are a warm physiotherapy intake assistant. "
                "Ask a brief, natural follow-up to collect any remaining details. "
                "Do NOT start with 'Thank you for sharing'. Output only the question."
            )
            question_text = llm_complete(prompt)

        # Deliver the question.
        # DIRECT_QUESTION: prefix → return verbatim (no LLM, preserves formatting).
        # Anything else → already the naturalised question from the LLM call above;
        # use it directly. Do NOT call llm_complete again — that would treat the
        # generated question as a prompt and produce meta-commentary ("I understand
        # you have been experiencing [placeholder]...") instead of the question.
        if question_text and question_text.startswith("DIRECT_QUESTION:"):
            response = question_text[len("DIRECT_QUESTION:"):]
        else:
            response = question_text or ""

        history.append({"role": "agent", "message": response})
        return {
            "response_text": response,
            "history": history,
        }

    return generate_question_node


def make_generate_summary_node(llm_complete):
    def generate_summary_node(state: InterviewState) -> dict:
        form = state["form"]
        history = list(state["history"])

        summary = generate_interview_summary(form, history, llm_complete)

        history.append({"role": "agent", "message": summary})
        return {
            "response_text": summary,
            "phase": "summary",
            "history": history,
        }

    return generate_summary_node
