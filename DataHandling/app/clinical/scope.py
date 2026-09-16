"""Deterministic clinical-scope boundary for the Stance MSK intake.

This service is an MSK intake workflow, not a general medical diagnosis tool.
Clear non-MSK presentations are therefore stopped before any form extraction or
summary generation.  The rules deliberately classify only clear wording; an
uncertain complaint is left for the normal, clinician-approved intake flow.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


POLICY_VERSION = "2026-09-16.1"

NON_MSK_INTAKE_MESSAGE = (
    "Stance Health's digital intake is designed for musculoskeletal concerns, "
    "such as joint, muscle, bone, movement, or injury problems. Your message "
    "appears to concern a health issue outside that scope, which this intake "
    "cannot assess safely. Please contact an appropriate qualified clinician. "
    "If you feel seriously unwell or need urgent help, call 112 in India or go "
    "to the nearest emergency department. This MSK assessment has not been completed."
)


@dataclass(frozen=True)
class ClinicalScopeDecision:
    """A non-emergency, out-of-scope intake decision."""

    category: str
    patient_message: str = NON_MSK_INTAKE_MESSAGE
    policy_version: str = POLICY_VERSION
    stop_interview: bool = True


# MSK wording takes precedence.  For example, "cold makes my knee pain worse"
# is relevant to an MSK assessment and must not be redirected just because it
# contains the word "cold".
_MSK_PATTERN = re.compile(
    r"\b(?:muscle|joint|bone|movement|mobility|injur(?:y|ies)|sprain|strain|"
    r"fracture|physio(?:therapy)?|rehab(?:ilitation)?|stiff(?:ness)?|swelling|"
    r"back|neck|shoulder|elbow|wrist|hand|finger|hip|knee|ankle|foot|"
    r"leg|arm|spine)\b",
    re.IGNORECASE,
)

_NON_MSK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("respiratory_or_fever", re.compile(
        r"\b(?:fever|high temperature|chills|cold|cough|sore throat|runny nose|"
        r"flu|breath(?:ing)? (?:problem|difficulty)|shortness of breath)\b",
        re.IGNORECASE,
    )),
    ("gastrointestinal", re.compile(
        r"\b(?:vomit(?:ing)?|diarrh(?:ea|oea)|loose motions?|stomach ache|"
        r"abdominal pain|food poisoning)\b",
        re.IGNORECASE,
    )),
    ("skin_or_allergy", re.compile(
        r"\b(?:rash|hives|allerg(?:y|ic)|skin infection)\b", re.IGNORECASE,
    )),
    ("eye_or_ear", re.compile(
        r"\b(?:eye pain|vision problem|blurred vision|earache|ear pain)\b",
        re.IGNORECASE,
    )),
    ("dental", re.compile(r"\b(?:toothache|tooth pain|dental pain|gum pain)\b", re.IGNORECASE)),
)


def assess_msk_intake_scope(text: str) -> ClinicalScopeDecision | None:
    """Return a routing decision only for an unambiguous non-MSK presentation.

    This is not diagnosis or emergency triage. Urgent-risk rules run separately
    and before this boundary.
    """

    normalized = " ".join(str(text or "").split())
    if not normalized or _MSK_PATTERN.search(normalized):
        return None

    for category, pattern in _NON_MSK_PATTERNS:
        if pattern.search(normalized):
            return ClinicalScopeDecision(category=category)
    return None
