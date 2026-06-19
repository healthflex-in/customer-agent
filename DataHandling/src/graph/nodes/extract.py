"""
Extract nodes for the LangGraph interview graph.

Optimised: classify_unanswered_fields gap-fill is folded inline (no
separate LLM node). Extraction and intent run sequentially on the same
LLM instance — concurrent calls caused silent failures.
"""
from typing import Callable

from src.graph.state import InterviewState
from src.graph.pure_functions.form_extraction import extract_form_data_from_text
from src.graph.pure_functions.form_validation import validate_section
from src.graph.pure_functions.intent_detection import should_check_for_correction
from src.graph.pure_functions.summary import classify_reports_intent
from src.prompts import FORMAT_PROMPT


def make_extract_node(llm_complete: Callable[[str], str]):
    """
    Combined node: runs form extraction AND intent classification concurrently,
    then applies the classify_unanswered_fields gap-fill inline.

    Replaces three sequential nodes (extract → classify_unanswered → classify_intent)
    with one node that does all three work items in ~max(extract_time, intent_time).

    Updates:
      - form                (extracted + gap-filled)
      - history             (user turn appended)
      - is_correction_turn
      - reports_intent
    """
    def extract_form_data_node(state: InterviewState) -> dict:
        user_input: str = state["user_input"]
        form: dict = state["form"]
        history: list = list(state["history"])
        current_section: str = state.get("current_section", "")
        awaiting_upload: bool = state.get("awaiting_report_upload", False)

        # ── Fast heuristic — if it's a correction, skip extraction LLM entirely ──
        if should_check_for_correction(user_input, awaiting_confirmation=False):
            new_history = history + [{"role": "user", "message": user_input}]
            return {
                "history": new_history,
                "is_correction_turn": True,
                "reports_intent": None,
            }

        # Prepare last agent question for intent context
        last_agent_q = ""
        for entry in reversed(history):
            if entry.get("role") == "agent":
                last_agent_q = entry.get("message", "")
                break

        # ── Run extraction + intent concurrently (separate LLM instances) ──────
        # Using ThreadPoolExecutor is safe here because each thread gets its own
        # LLM instance via the llm_complete closure — no shared mutable state.
        from concurrent.futures import ThreadPoolExecutor

        def _run_extraction():
            try:
                result = extract_form_data_from_text(
                    user_input=user_input,
                    form=form,
                    prompt_template=FORMAT_PROMPT,
                    llm_complete=llm_complete,
                    current_section=current_section,
                )
                filled = {k: v for sec in result.values() if isinstance(sec, dict)
                          for k, v in sec.items() if v and str(v).strip()}
                print(f"[extract_node] Extraction done. Filled {len(filled)} fields: {list(filled.keys())[:5]}")
                return result
            except Exception as _ex:
                print(f"[extract_node] Extraction failed: {_ex}")
                return form

        def _run_intent():
            if awaiting_upload:
                return {}
            return classify_reports_intent(
                user_input=user_input,
                llm_complete=llm_complete,
                context_question=last_agent_q,
            )

        # Intent classification has a fast-path for most turns (no LLM needed),
        # so the overhead of a thread is only paid when the LLM is actually called.
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_extract = pool.submit(_run_extraction)
            fut_intent = pool.submit(_run_intent)
            updated_form = fut_extract.result()
            reports_intent = fut_intent.result()

        # ── Inline gap-fill (replaces classify_unanswered_fields LLM call) ───────
        # Only triggers when there are still missing fields after extraction,
        # using a single focused LLM call limited to the current section.
        missing = [
            f for f in validate_section(updated_form, current_section)
            if "(If Any)" not in f and "(Optional)" not in f
        ]

        if missing:
            last_agent_q = ""
            for entry in reversed(history):
                if entry.get("role") == "agent":
                    last_agent_q = entry.get("message", "")
                    break

            filled_facts = [
                f"{field}: {value}"
                for sec_data in updated_form.values()
                if isinstance(sec_data, dict)
                for field, value in sec_data.items()
                if value and str(value).strip()
            ]
            context = "; ".join(filled_facts[:6]) if filled_facts else "Nothing collected yet"
            missing_str = "\n".join(f'  - "{f}"' for f in missing)

            gap_prompt = f"""Medical intake assistant analysing a patient reply.

Context: {context}
Assistant asked: "{last_agent_q}"
Patient replied: "{user_input}"

Still empty in "{current_section}":
{missing_str}

For EACH field: did the reply address it?
- If YES → write the exact value (use their words).
- If they negated it ("nope", "no", "never", "haven't") → write a clear negative value like "None", "No previous consultations", "Not applicable".
- If irrelevant → return null.

Respond ONLY with JSON: {{"Field Name": "value or null"}}"""

            try:
                import json as _json
                raw = llm_complete(gap_prompt).strip()
                start, end = raw.find("{"), raw.rfind("}")
                if start != -1 and end != -1:
                    classified = _json.loads(raw[start:end + 1])
                    for field, value in classified.items():
                        if value is not None and str(value).strip():
                            if current_section in updated_form and field in updated_form[current_section]:
                                existing = updated_form[current_section].get(field, "")
                                if not existing or not str(existing).strip():
                                    updated_form[current_section][field] = str(value).strip()
                                    print(f"[gap_fill] {field} → {value}")
            except Exception as _e:
                print(f"[gap_fill] Non-fatal error: {_e}")

        new_history = history + [{"role": "user", "message": user_input}]

        return {
            "form": updated_form,
            "history": new_history,
            "is_correction_turn": False,
            "reports_intent": reports_intent,
        }

    return extract_form_data_node


def make_classify_intent_node(llm_complete: Callable[[str], str]):
    """No-op stub — intent classification is now handled inside make_extract_node."""
    def classify_intent_node(state: InterviewState) -> dict:
        return {}
    return classify_intent_node
