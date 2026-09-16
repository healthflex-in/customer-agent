"""Deterministic replies when a patient asks what an intake question means."""

from __future__ import annotations

import re


_CLARIFICATION_PATTERNS = (
    r"\bwhat (?:do|did|would) you (?:want|like|need) to know\b",
    r"\bwhat (?:exactly|basically) (?:do|did|would) you (?:want|mean|need)\b",
    r"\bwhat (?:information|details) (?:do|would) you (?:want|need)\b",
    r"\bwhich (?:information|details) (?:do|would) you (?:want|need)\b",
    r"\bcan you (?:clarify|explain) (?:that|the question|what you mean)\b",
    r"\bwhy (?:do|are) you (?:need|asking|ask)\b",
)


def build_intake_clarification(
    user_input: str | None,
    last_agent_question: str | None,
) -> str | None:
    """Return a focused explanation without advancing the interview.

    The last assistant question is included only to identify the topic. The
    patient message must itself contain a clear request for clarification, so
    ordinary clinical answers are never intercepted.
    """

    patient_text = " ".join(str(user_input or "").lower().split())
    if not patient_text or not any(
        re.search(pattern, patient_text) for pattern in _CLARIFICATION_PATTERNS
    ):
        return None

    topic_text = f"{patient_text} {str(last_agent_question or '').lower()}"

    if any(term in topic_text for term in ("surgery", "surgeries", "operation", "procedure")):
        return (
            "For each surgery, please tell me what procedure you had and which body part "
            "it involved, why it was needed, approximately when it happened, and whether "
            "you have any ongoing symptoms, complications, or activity restrictions. "
            "A short answer is completely fine."
        )
    if any(term in topic_text for term in ("exercise", "workout", "activity level", "physically active")):
        return (
            "Please share the type of exercise or activity you do, how many days per week "
            "you do it, and whether your current condition limits it. A brief answer is fine."
        )
    if any(term in topic_text for term in ("health condition", "illness", "medical condition")):
        return (
            "Please mention any ongoing or important past health condition, when it was "
            "diagnosed, and whether you currently take treatment or have any related limitations."
        )
    if any(term in topic_text for term in ("report", "x-ray", "x ray", "mri", "scan")):
        return (
            "Please tell me what report or scan you have, which body area it is for, when it "
            "was done, and whether you were told the main finding. You can upload it when prompted."
        )
    if any(term in topic_text for term in ("goal", "expect", "treatment")):
        return (
            "Please describe what improvement you want from treatment—for example less pain, "
            "better movement, returning to work, exercise, sport, or another daily activity."
        )
    if any(term in topic_text for term in ("pain", "complaint", "symptom")):
        return (
            "Please describe where the problem is, how severe it feels, when and how it started, "
            "and what makes it worse or better."
        )

    return (
        "Please share only what you are comfortable sharing. I am looking for the basic details "
        "requested in the previous question; a short, approximate answer is fine."
    )
