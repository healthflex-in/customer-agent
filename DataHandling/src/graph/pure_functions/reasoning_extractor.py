"""
PROM (FRM-02+) form extractor.

Reads the full conversation and fills the outcome-measure form. FRM-01 intake
answers are mapped by ``answer_mapping`` instead.
"""
import copy
import json
from typing import Callable, Optional

from app.observability.privacy import error_type
from src.prompts import PROM_EXTRACTOR_PROMPT


_NON_CLINICAL_AGENT_PREFIXES = (
    "I'm here to collect information for your clinical assessment",
    "I’m here to collect information for your clinical assessment",
)


def _clinical_history(history: list) -> list:
    """Remove boundary/FAQ turns before clinical extraction.

    These turns remain in the visible transcript, but must never become
    evidence for a clinical form field. The prefix fallback also protects
    sessions created before the explicit marker was deployed.
    """
    excluded: set[int] = {
        index
        for index, entry in enumerate(history)
        if entry.get("clinical_extraction") is False
    }
    for index, entry in enumerate(history):
        message = str(entry.get("message") or "").strip()
        if entry.get("role") != "agent" or not message.startswith(
            _NON_CLINICAL_AGENT_PREFIXES
        ):
            continue
        excluded.add(index)
        if index and history[index - 1].get("role") == "user":
            excluded.add(index - 1)
    return [entry for index, entry in enumerate(history) if index not in excluded]


def _build_conversation_text(history: list, user_input: str) -> str:
    """Build a readable conversation string including the current user message."""
    lines = []
    for entry in _clinical_history(history):
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
    Use the reasoning LLM to read the full conversation and fill the PROM form.

    Returns an updated form dict, or None if extraction failed (caller should
    fall back to the existing extraction pipeline).
    """
    try:
        conversation = _build_conversation_text(history, user_input)
        current_form = copy.deepcopy(form)
        form_structure = json.dumps(current_form, indent=2)

        prompt = PROM_EXTRACTOR_PROMPT.format(
            conversation=conversation,
            current_form=form_structure,
            form_structure=form_structure,
        )
        import time as _time
        _t0 = _time.perf_counter()
        raw = reasoning_llm(prompt).strip()
        print(f"[timing] reasoning_extract_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms "
              f"prompt_chars={len(prompt)}")

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
        print(f"[reasoning_extract] Failed with {error_type(e)}; using regular extraction")
        return None
