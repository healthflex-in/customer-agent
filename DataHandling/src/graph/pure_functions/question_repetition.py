"""Detect generated intake questions that repeat already-filled fields."""

from __future__ import annotations


_FIELD_TOPIC_TERMS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "History & Diagnostics",
        "Current Lifestyle",
        (
            "smok", "alcohol", "drink alcohol", "exercise", "workout",
            "activity level", "physically active", "occupation", "your job",
        ),
    ),
    (
        "History & Diagnostics",
        "Reports",
        (
            "x-ray", "x ray", "xray", "mri", "ct scan", "diagnostic report",
            "reports", "scans", "imaging", "upload",
        ),
    ),
    (
        "History & Diagnostics",
        "Systemic Illness and Surgical History",
        ("surgery", "surgeries", "operation", "health condition", "medical condition"),
    ),
    (
        "Previous Consultations",
        "Previous Diagnosis or Advice and Prescribed Treatment Taken",
        ("seen a doctor", "consulted", "physiotherapist", "previous treatment"),
    ),
    (
        "Pain Assessment",
        "Severity (1-10)",
        ("scale of 0 to 10", "scale of 1 to 10", "pain severity", "rate your pain"),
    ),
    (
        "Pain Assessment",
        "Aggravating Factors",
        ("makes it worse", "aggravating", "worsens your"),
    ),
    (
        "Pain Assessment",
        "Relieving Factors",
        ("provides relief", "makes it better", "relieving", "helps your pain"),
    ),
    (
        "Treatment Goals",
        "Short-Term Goals (within 3 months)",
        ("next 3 months", "short-term goal", "short term goal"),
    ),
    (
        "Treatment Goals",
        "Long-Term Goals (after 3 months)",
        ("long-term goal", "long term goal", "ultimate goal"),
    ),
)


def asks_about_answered_field(question: str | None, form: dict) -> bool:
    """Return true when generated text asks a topic whose field is populated."""

    normalized = " ".join(str(question or "").lower().split())
    if not normalized:
        return False

    for section, field, terms in _FIELD_TOPIC_TERMS:
        value = form.get(section, {}).get(field, "")
        if value is None or not str(value).strip():
            continue
        if any(term in normalized for term in terms):
            return True
    return False
