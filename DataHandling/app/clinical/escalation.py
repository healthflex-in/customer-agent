"""Versioned, deterministic urgent-risk detection before AI processing.

This module is intentionally independent from an LLM.  Its built-in rules are a
conservative safety floor, not a diagnosis or a replacement for clinical
triage.  Rule changes and patient-facing wording require clinical-owner review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Mapping, Protocol, Sequence


ASCENDING = 1
DESCENDING = -1


class EscalationCollection(Protocol):
    """Small collection surface used here; keeps risk assessment dependency-free."""

    def create_index(self, keys: list[tuple[str, int]], **kwargs: Any) -> Any: ...
    def insert_one(self, document: Mapping[str, Any]) -> Any: ...


POLICY_VERSION = "2026-09-03.1"
MENTAL_HEALTH_MESSAGE = (
    "Your response may indicate an urgent safety concern. This automated intake "
    "cannot provide emergency care. If you may act on thoughts of self-harm or are "
    "in immediate danger, call 112 or go to the nearest emergency department now. "
    "For mental-health support in India, call Tele-MANAS at 14416. If it is safe "
    "to do so, ask someone you trust to stay with you. Please stop this intake and "
    "contact a qualified professional now."
)
NEUROLOGICAL_EMERGENCY_MESSAGE = (
    "Your response may indicate an urgent neurological warning sign. This automated "
    "intake cannot assess an emergency. Please stop the intake and go to the nearest "
    "emergency department now, or call 112 if you need emergency assistance."
)


@dataclass(frozen=True)
class EscalationDecision:
    rule_id: str
    category: str
    severity: str
    patient_message: str
    policy_version: str = POLICY_VERSION
    stop_interview: bool = True


_SELF_HARM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bkill\s+myself\b",
        r"\bend\s+my\s+(?:own\s+)?life\b",
        r"\btake\s+my\s+own\s+life\b",
        r"\bcommit\s+suicide\b",
        r"\bsuicidal\b",
        r"\b(?:hurt|harm)\s+myself\b",
        r"\bbetter\s+off\s+dead\b",
        r"\bwish\s+i\s+(?:was|were)\s+dead\b",
        r"\b(?:do\s+not|don'?t)\s+want\s+to\s+live\b",
        r"\bi\s+(?:want|wanna)\s+to\s+die\b",
    )
)
_NEGATED_SELF_HARM_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^(?:i\s+(?:am|'m)\s+)?(?:not|never)\s+suicidal[.! ]*$",
        r"^(?:i\s+have\s+)?no\s+(?:suicidal|self[- ]harm)\s+thoughts?[.! ]*$",
        r"^i\s+(?:do\s+not|don'?t)\s+want\s+to\s+die[.! ]*$",
        r"^i\s+(?:would\s+not|wouldn'?t|won'?t)\s+(?:hurt|harm|kill)\s+myself[.! ]*$",
    )
)
_BLADDER_BOWEL_PATTERN = re.compile(
    r"\b(?:loss|lost|lose|losing|unable|can(?:not|'t)|difficulty|control|retention|"
    r"incontinence|leak(?:ing|age)?)\b.{0,45}\b(?:bladder|bowel|urine|urinate|pee|"
    r"stool|defecat(?:e|ion))\b|\b(?:bladder|bowel|urine|urination)\b.{0,45}\b"
    r"(?:control|retention|incontinence|leak(?:ing|age)?|unable|can(?:not|'t))\b"
)
_SADDLE_PATTERN = re.compile(
    r"\b(?:numb(?:ness)?|loss\s+of\s+(?:feeling|sensation)|tingl(?:e|ing))\b.{0,45}"
    r"\b(?:saddle|groin|genitals?|inner\s+thighs?|buttocks?|back\s+passage|between\s+"
    r"(?:my\s+)?legs)\b|\b(?:saddle|groin|genitals?|inner\s+thighs?|buttocks?|"
    r"between\s+(?:my\s+)?legs)\b.{0,45}\b(?:numb(?:ness)?|loss\s+of\s+"
    r"(?:feeling|sensation)|tingl(?:e|ing))\b"
)
_PHQ9_POSITIVE = {
    "several days",
    "more than half the days",
    "nearly every day",
}


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _is_phq9_item9(question_id: Any, question_text: Any) -> bool:
    qid = _normalise(question_id).replace("-", "_")
    text = _normalise(question_text)
    id_match = qid.startswith("prom_phq9") and bool(
        re.search(r"(?:^|_)(?:9|q9|item9|item_9)(?:_|$)", qid)
    )
    text_match = (
        "better off dead" in text
        or "hurting yourself" in text
        or "harm yourself" in text
        or "self-harm" in text
        or "suicidal thoughts" in text
    )
    return id_match or (qid.startswith("prom_phq9") and text_match)


def _positive_phq9_item9(
    text: str,
    question_meta: Mapping[str, Any] | None,
    structured_answers: Sequence[str] | None,
) -> bool:
    if not isinstance(question_meta, Mapping):
        return False

    ids = question_meta.get("question_ids")
    questions = question_meta.get("questions")
    if structured_answers is not None and isinstance(ids, (list, tuple)):
        question_texts = questions if isinstance(questions, (list, tuple)) else []
        for index, answer in enumerate(structured_answers):
            qid = ids[index] if index < len(ids) else None
            prompt = question_texts[index] if index < len(question_texts) else None
            if _is_phq9_item9(qid, prompt) and _normalise(answer) in _PHQ9_POSITIVE:
                return True
        return False

    if isinstance(ids, (list, tuple)) and len(ids) == 1:
        question_texts = questions if isinstance(questions, (list, tuple)) else []
        prompt = question_texts[0] if question_texts else None
        return _is_phq9_item9(ids[0], prompt) and _normalise(text) in _PHQ9_POSITIVE

    qid = question_meta.get("question_id")
    prompt = question_meta.get("question") or question_meta.get("text")
    return _is_phq9_item9(qid, prompt) and _normalise(text) in _PHQ9_POSITIVE


def assess_urgent_risk(
    text: str,
    *,
    question_meta: Mapping[str, Any] | None = None,
    structured_answers: Sequence[str] | None = None,
) -> EscalationDecision | None:
    """Return the first deterministic urgent-risk decision, if any."""

    normalised = _normalise(text)
    if _positive_phq9_item9(normalised, question_meta, structured_answers):
        return EscalationDecision(
            rule_id="PHQ9_ITEM9_POSITIVE",
            category="self_harm",
            severity="urgent",
            patient_message=MENTAL_HEALTH_MESSAGE,
        )

    if not any(pattern.search(normalised) for pattern in _NEGATED_SELF_HARM_PATTERNS):
        if any(pattern.search(normalised) for pattern in _SELF_HARM_PATTERNS):
            return EscalationDecision(
                rule_id="EXPLICIT_SELF_HARM_LANGUAGE",
                category="self_harm",
                severity="urgent",
                patient_message=MENTAL_HEALTH_MESSAGE,
            )

    if _BLADDER_BOWEL_PATTERN.search(normalised) and _SADDLE_PATTERN.search(normalised):
        return EscalationDecision(
            rule_id="CAUDA_EQUINA_COMBINATION",
            category="acute_neurological",
            severity="emergency",
            patient_message=NEUROLOGICAL_EMERGENCY_MESSAGE,
        )
    return None


def ensure_escalation_indexes(collection: EscalationCollection) -> None:
    collection.create_index(
        [("patientId", ASCENDING), ("createdAt", DESCENDING)],
        name="patient_escalations_created_at",
    )
    collection.create_index(
        [("status", ASCENDING), ("createdAt", ASCENDING)],
        name="open_escalations_created_at",
    )


def record_escalation(
    collection: EscalationCollection,
    decision: EscalationDecision,
    *,
    patient_id: str,
    form_id: str | None,
    attempt_id: str | None,
    session_id: str | None,
    request_id: str | None,
) -> Any:
    """Persist actionable metadata without storing or logging the triggering text."""

    event = {
        "patientId": patient_id,
        "formId": form_id,
        "attemptId": attempt_id,
        "sessionId": session_id,
        "requestId": request_id,
        "ruleId": decision.rule_id,
        "category": decision.category,
        "severity": decision.severity,
        "policyVersion": decision.policy_version,
        "status": "detected",
        "createdAt": datetime.now(timezone.utc),
    }
    return collection.insert_one(event).inserted_id
