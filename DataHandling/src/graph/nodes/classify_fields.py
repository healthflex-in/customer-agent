"""
Node: classify_unanswered_fields

Sits between extract_form_data and classify_intent. After the main extraction
LLM run, some fields may still be empty even though the patient answered them —
e.g. "nope not seen anyone" (negation), "gradually" (short answer), or
"i'm new to this" (indirect). This node makes ONE targeted LLM call to fill
those gaps intelligently, covering all empty fields in the current section.
"""
import json
from typing import Callable

from src.graph.state import InterviewState
from src.graph.pure_functions.form_validation import validate_section


def make_classify_unanswered_fields_node(llm_complete: Callable[[str], str]):
    def classify_unanswered_fields_node(state: InterviewState) -> dict:
        current_section = state["current_section"]
        form = state["form"]
        user_input = state["user_input"]
        history = list(state["history"])

        # Get the missing required fields for the current section
        missing = [
            f for f in validate_section(form, current_section)
            if "(If Any)" not in f and "(Optional)" not in f
        ]

        if not missing:
            # Nothing to classify — section already complete
            return {}

        # Find the last question the agent asked (gives LLM context on what was asked)
        last_agent_question = ""
        for entry in reversed(history):
            if entry.get("role") == "agent":
                last_agent_question = entry.get("message", "")
                break

        # Build a concise summary of what's already filled
        filled_facts = [
            f"{field}: {value}"
            for sec_data in form.values()
            if isinstance(sec_data, dict)
            for field, value in sec_data.items()
            if value and str(value).strip()
        ]
        context = "; ".join(filled_facts[:6]) if filled_facts else "Nothing collected yet"

        missing_fields_str = "\n".join(f'  - "{f}"' for f in missing)

        prompt = f"""You are a medical intake assistant analysing a patient's reply.

Context already collected: {context}

The assistant just asked: "{last_agent_question}"

The patient replied: "{user_input}"

The following fields in the "{current_section}" section are still empty after initial extraction:
{missing_fields_str}

For EACH empty field, decide:
1. Did the patient's reply address this field (directly OR indirectly)?
2. If YES  → write the exact value to store (use their words, cleaned up).
3. If they NEGATED it (e.g. "nope", "no", "none", "haven't", "never", "not seen anyone") → write an appropriate negative value like "None", "No previous consultations", "Not applicable", etc.
4. If their reply is irrelevant to the field → return null.

Respond ONLY with a JSON object mapping each field name to its value or null.
Example: {{"Field Name": "extracted value", "Other Field": null}}

JSON:"""

        try:
            raw = llm_complete(prompt)
            # Extract JSON from response
            raw = raw.strip()
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                return {}
            json_str = raw[start:end + 1]
            classified = json.loads(json_str)
        except Exception as e:
            print(f"[classify_unanswered_fields] LLM/parse error (non-fatal): {e}")
            return {}

        # Apply non-null values to the form
        updated_form = {
            section: dict(fields) if isinstance(fields, dict) else fields
            for section, fields in form.items()
        }
        updates_made = []
        for field, value in classified.items():
            if value is not None and str(value).strip():
                if current_section in updated_form and field in updated_form[current_section]:
                    existing = updated_form[current_section].get(field, "")
                    if not existing or not str(existing).strip():
                        updated_form[current_section][field] = str(value).strip()
                        updates_made.append(f"{field} → {value}")

        if updates_made:
            print(f"[classify_unanswered_fields] Filled: {'; '.join(updates_made)}")
            return {"form": updated_form}

        return {}

    return classify_unanswered_fields_node
