"""Capture additional symptoms without replacing an existing complaint."""
import copy
import re


def is_explicit_symptom_addition(text):
    lowered = text.lower()
    return bool(
        re.search(r"\b(?:pain|stiffness|weakness|swelling)\b", lowered)
        and re.search(r"\b(?:as well|aswell|also|additional|forgot|add)\b", lowered)
        and not re.search(r"\b(?:no pain|not pain|dont have|don't have|do not have)\b", lowered)
    )


def split_update_and_addition(text):
    """Split `change X and I also have Y` without losing either action."""
    match = re.search(
        r"\b(?:and\s+)?(?:i\s+)?(?:also|additionally)\s+(?:have|feel|experience)\b",
        text,
        re.I,
    )
    if not match:
        return None
    update_text = text[:match.start()].strip(" ,.;")
    addition_text = text[match.start():].strip(" ,.;")
    if not update_text or not is_explicit_symptom_addition(addition_text):
        return None
    if not re.search(r"\b(?:update|change|correct|make|set|should be|instead)\b", update_text, re.I):
        return None
    return update_text, addition_text


def capture_additional_complaint(form, text):
    updated = copy.deepcopy(form)
    # Keep the exact patient wording as the source of truth. Details about the
    # original complaint must not be reused for this additional symptom.
    existing = [name for name in updated if name.startswith("Additional Complaint ")]
    for name in existing:
        if updated[name].get("Primary Complaint") == text.strip():
            return updated, name
    name = f"Additional Complaint {len(existing) + 1}"
    updated[name] = {
        "Primary Complaint": text.strip(),
        "Duration of the Issue": "",
        "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "",
        "Severity (1-10)": "",
        "Aggravating Factors": "",
        "Relieving Factors": "",
    }
    return updated, name
