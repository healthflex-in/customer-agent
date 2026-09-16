"""Deterministic multi-field consistency for clinical corrections."""

from __future__ import annotations

import copy
import re


_BODY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Head", ("head", "headache", "migraine")),
    ("Neck", ("neck",)),
    ("Back", ("back", "spine")),
    ("Shoulder", ("shoulder", "shoulders")),
    ("Arm", ("arm", "arms", "hand", "hands", "elbow", "elbows")),
    ("Leg", ("leg", "legs", "knee", "knees", "ankle", "ankles", "foot", "feet")),
    ("Hip", ("hip", "hips")),
)


def _body_locations(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for label, terms in _BODY_TERMS:
        if any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms):
            found.append(label)
    return found


def detect_pain_location_correction(user_input: str, form: dict) -> dict | None:
    """Recognize explicit “not there; only here” pain-location corrections."""

    lowered = " ".join(user_input.lower().replace("’", "'").split())
    if "pain" not in lowered:
        return None

    clauses = [
        clause.strip()
        for clause in re.split(r"[.!?;]+|\bbut\b", lowered)
        if clause.strip()
    ]
    excluded: list[str] = []
    positive: list[str] = []
    for clause in clauses:
        locations = _body_locations(clause)
        is_negative = bool(
            re.search(
                r"\b(?:do not|don't|dont|no|not|never)\b.{0,30}\bpain\b"
                r"|\bpain\b.{0,20}\b(?:not|none)\b",
                clause,
            )
        )
        if is_negative:
            excluded.extend(locations)
        elif "pain" in clause:
            positive.extend(locations)

    excluded = list(dict.fromkeys(excluded))
    positive = [item for item in dict.fromkeys(positive) if item not in excluded]
    if not excluded or not positive:
        return None

    new_location = " and ".join(positive)
    old_value = form.get("Pain Assessment", {}).get(
        "Primary Location of Pain", ""
    )
    return {
        "is_correction": True,
        "confidence": "high",
        "old_value": old_value,
        "new_value": f"{new_location} only",
        "field_name": "Primary Location of Pain",
        "section_name": "Pain Assessment",
        "needs_clarification": False,
        "clarification_question": "",
        "excluded_pain_locations": excluded,
        "corrected_pain_locations": positive,
        "source_text": user_input,
    }


def _contains_location(value: object, locations: list[str]) -> bool:
    lowered = str(value or "").lower()
    for label, terms in _BODY_TERMS:
        if label not in locations:
            continue
        if any(re.search(rf"\b{re.escape(term)}\b", lowered) for term in terms):
            return True
    return False


def _replace_locations(value: str, excluded: list[str], replacement: str) -> str:
    result = value
    for label, terms in _BODY_TERMS:
        if label not in excluded:
            continue
        for term in sorted(terms, key=len, reverse=True):
            result = re.sub(
                rf"\b{re.escape(term)}\b",
                replacement.lower(),
                result,
                flags=re.IGNORECASE,
            )
    duplicate = rf"\b{re.escape(replacement.lower())}\b\s*(?:and|or|/|,)\s*\b{re.escape(replacement.lower())}\b"
    while re.search(duplicate, result, flags=re.IGNORECASE):
        result = re.sub(duplicate, replacement.lower(), result, flags=re.IGNORECASE)
    return " ".join(result.split()).strip(" ,")


def reconcile_pain_location_dependencies(
    form: dict,
    correction_data: dict,
) -> tuple[dict, list[str]]:
    """Update/clear fields that contradict an explicit pain-location correction."""

    excluded = correction_data.get("excluded_pain_locations") or []
    corrected = correction_data.get("corrected_pain_locations") or []
    if not excluded or not corrected:
        return form, []

    updated = copy.deepcopy(form)
    replacement = " and ".join(corrected)
    changes: list[str] = []

    pain = updated.get("Pain Assessment", {})
    if isinstance(pain, dict):
        pain["Primary Location of Pain"] = f"{replacement} only"
        for field in ("Aggravating Factors", "Relieving Factors"):
            if _contains_location(pain.get(field), excluded):
                pain[field] = ""
                changes.append(f"cleared the old {field.lower()}")

    complaint = updated.get("Present Complaint", {})
    if isinstance(complaint, dict):
        primary = str(complaint.get("Primary Complaint", "") or "")
        if _contains_location(primary, excluded):
            complaint["Primary Complaint"] = f"Pain in {replacement.lower()} only"
            changes.append("updated the primary complaint location")
        mechanism = complaint.get("Mechanism of Injury or Cause", "")
        if _contains_location(mechanism, excluded):
            complaint["Mechanism of Injury or Cause"] = ""
            changes.append("cleared the old injury mechanism")

    goals = updated.get("Treatment Goals", {})
    if isinstance(goals, dict):
        for field, value in list(goals.items()):
            if value and _contains_location(value, excluded):
                goals[field] = _replace_locations(str(value), excluded, replacement)
                changes.append(f"updated {field.lower()}")

    return updated, list(dict.fromkeys(changes))
