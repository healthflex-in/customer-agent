"""Deterministic referral-question and referral-answer handling.

Referral is a small, constrained intake field.  Keeping its recognition in one
pure module prevents the question generator, extractor, resume path, and summary
correction path from assigning different meanings to the same patient reply.
"""

from __future__ import annotations

import re


_REFERRAL_QUESTION_PATTERNS = (
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
