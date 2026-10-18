"""Deterministic referral-question and referral-answer handling.

Referral is a small, constrained intake field.  Keeping its recognition in one
pure module prevents the question generator, extractor, resume path, and summary
correction path from assigning different meanings to the same patient reply.
"""

from __future__ import annotations

import re


_REFERRAL_QUESTION_PATTERNS = (
    r"\bwhere did you hear about us\b",
    r"\bhow did you (?:come to know|hear|find|learn) about\b",
    r"\bhow (?:did|have) you (?:hear|heard|find|found) (?:about|us)\b",
    r"\bhow (?:did|have) you (?:come across|discover)\b",
    r"\bhow were you referred\b",
    r"\bhow did you hear of us\b",
    r"\breferral source\b",
    r"\bwho referred you\b",
)

# More specific phrases must precede their shorter components.
_REFERRAL_CHANNELS = (
    ("doctor referred", "Doctor referral"),
    ("referred by doctor", "Doctor referral"),
    ("word of mouth", "Word of mouth"),
    ("physiotherapist", "Physiotherapist referral"),
    ("social media", "Social media"),
    ("stance health website", "Stance Health website"),
    ("youtube", "YouTube"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("whatsapp", "WhatsApp"),
    ("twitter", "Twitter"),
    ("google", "Google"),
    ("website", "Stance Health website"),
    ("online", "Found online"),
    ("friend", "Friend/word of mouth"),
    ("family", "Family referral"),
    ("relative", "Family referral"),
    ("colleague", "Colleague referral"),
    ("newspaper", "Newspaper"),
    ("physician", "Doctor referral"),
    ("doctor", "Doctor referral"),
    ("hospital", "Hospital referral"),
    ("podcast", "Podcast"),
    ("blog", "Blog/article"),
)


def referral_answer(question: str | None, user_input: str | None) -> str | None:
    """Return the clause that answers where the patient heard about us.

    In a batch, the activity-and-habits question takes two comma-separated
    clauses. The source clause is the one after those, and it is not taken
    from an activity or habit answer.
    """

    text = " ".join(str(user_input or "").strip().split())
    if not text or not is_referral_question(question):
        return None
    bullets = [
        line.strip()[2:].strip()
        for line in str(question or "").splitlines()
        if line.strip().startswith("- ")
    ]
    if not bullets:
        return text
    clauses = [part.strip() for part in re.split(r"\s*[,;]\s*", text) if part.strip()]
    index = 0
    for bullet in bullets:
        normalized = " ".join(bullet.lower().split())
        if is_referral_question(bullet):
            if index >= len(clauses):
                return None
            return clauses[index]
        index += (
            2
            if "activity or exercise" in normalized and "smoke or drink" in normalized
            else 1
        )
    return None


def is_referral_question(text: str | None) -> bool:
    """Return whether assistant text asks how the patient found the clinic."""

    normalized = " ".join(str(text or "").lower().split())
    return any(re.search(pattern, normalized) for pattern in _REFERRAL_QUESTION_PATTERNS)


def normalize_referral_source(text: str | None) -> str | None:
    """Return a normalized known referral channel, or ``None``.

    Unknown free text is deliberately not accepted here: a symptom or treatment
    answer must never be written into ``Referral.Source`` merely because state
    incorrectly says that the referral question was active.
    """

    normalized = " ".join(str(text or "").lower().strip().split())
    if not normalized:
        return None

    for phrase, label in _REFERRAL_CHANNELS:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalized):
            return label
    return None
