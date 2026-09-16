"""Deterministic required-field questions for the FRM-01 intake."""

from __future__ import annotations


# Referral is intentionally excluded: it has one dedicated final-question flow.
REQUIRED_FIELD_QUESTIONS = {
    "Primary Complaint": "What is the main problem bothering you?",
    "Duration of the Issue": "How long have you been experiencing this issue?",
    "Onset (Gradual or Sudden)": "Did it start suddenly or gradually?",
    "Mechanism of Injury or Cause": "What caused it or what was happening when it started?",
    "Previous Diagnosis or Advice and Prescribed Treatment Taken": (
        "Have you seen a doctor, physiotherapist, or hospital for this problem before? "
        "If yes, what diagnosis, advice, or treatment did they give?"
    ),
    "Current Status of Issue (Improved, Same, Worse)": (
        "After that previous consultation or treatment, is the condition improved, "
        "the same, or worse?"
    ),
    "Primary Location of Pain": "Where exactly do you feel the pain or problem?",
    "Severity (1-10)": "On a scale of 0 to 10, how severe is it right now?",
    "Aggravating Factors": "What activities, movements, or situations make it worse?",
    "Relieving Factors": "What makes it feel better or gives relief?",
    "Systemic Illness and Surgical History": (
        "Do you have any other health conditions, past surgeries, or fractures?"
    ),
    "Current Lifestyle": (
        "What is your work and usual activity or exercise routine? Do you smoke or drink alcohol?"
    ),
    "Reports": "Do you have any related X-rays, MRI, CT scans, or blood reports?",
    "Short-Term Goals (within 3 months)": (
        "What would you like to achieve in the next three months?"
    ),
    "Long-Term Goals (after 3 months)": (
        "What is your longer-term health or activity goal after three months?"
    ),
    "Specific Expectations from Treatment": (
        "What are your specific expectations from treatment?"
    ),
}


def question_for_missing_fields(
    missing: list[tuple[str, str]], field_labels: dict[str, str], *, max_fields: int = 4
) -> str:
    """Return an explicit question for each missing field in one small batch."""

    prompts = [
        REQUIRED_FIELD_QUESTIONS.get(field, field_labels.get(field, field))
        for _, field in missing[:max_fields]
    ]
    if len(prompts) == 1:
        return prompts[0]
    return "Could you also tell me:\n" + "\n".join(f"- {prompt}" for prompt in prompts)
