"""
Reasoning-based form extractor.

Uses gemini-2.5-flash to reason through the full conversation and fill the
medical intake form with genuine understanding — not keyword matching or
multi-pass heuristics.

Called once per turn with the full conversation history, replacing the
FORMAT_PROMPT + gap_fill + sweep_fill + comprehensive_fill stack.
"""
import copy
import json
from typing import Callable, Optional

from src.prompts import REASONING_EXTRACTOR_PROMPT


def _build_conversation_text(history: list, user_input: str) -> str:
    """Build a readable conversation string including the current user message."""
    lines = []
    for entry in history:
        role = "Sage" if entry.get("role") == "agent" else "Patient"
        msg = (entry.get("message") or "").strip()
        if msg:
            lines.append(f"{role}: {msg}")
    lines.append(f"Patient: {user_input}")
    return "\n".join(lines)


def reasoning_extract(
    user_input: str,
    history: list,
    form: dict,
    reasoning_llm: Callable[[str], str],
) -> Optional[dict]:
    """
    Use the reasoning LLM to read the full conversation and fill the form.

    Returns an updated form dict, or None if extraction failed (caller should
    fall back to the existing extraction pipeline).
    """
    try:
        conversation = _build_conversation_text(history, user_input)
        current_form = copy.deepcopy(form)
        form_structure = json.dumps(current_form, indent=2)

        prompt = REASONING_EXTRACTOR_PROMPT.format(
            conversation=conversation,
            current_form=form_structure,
            form_structure=form_structure,
        )

        raw = reasoning_llm(prompt).strip()

        # Extract JSON from response
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end == -1:
            print("[reasoning_extract] No JSON found in response")
            return None

        filled = json.loads(raw[start:end + 1])
        if not isinstance(filled, dict):
            return None

        # Merge into current form — only update fields, never remove sections
        result = copy.deepcopy(current_form)
        for section, fields in filled.items():
            if section in result and isinstance(fields, dict):
                for field, value in fields.items():
                    if field in result[section]:
                        # Accept any non-null value including explicit "None" strings
                        if value is not None:
                            result[section][field] = str(value).strip() if value != "" else ""

        filled_count = sum(
            1 for sec in result.values() if isinstance(sec, dict)
            for v in sec.values() if v and str(v).strip()
        )
        print(f"[reasoning_extract] Filled {filled_count} fields from conversation")
        return result

    except Exception as e:
        print(f"[reasoning_extract] Failed ({e}), falling back to regular extraction")
        return None
