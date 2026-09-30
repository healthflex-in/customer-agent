"""Deterministic extraction for common lifestyle statements."""

from __future__ import annotations

import re


LIFESTYLE_COMPONENT_FIELDS = {
    "work": "Current Lifestyle — Work",
    "activity": "Current Lifestyle — Activity or Exercise",
    "smoking": "Current Lifestyle — Smoking",
    "alcohol": "Current Lifestyle — Alcohol",
}


def lifestyle_components(value: str | None) -> set[str]:
    """Return the explicitly documented lifestyle subtopics."""

    text = " ".join(str(value or "").lower().replace("’", "'").split())
    found: set[str] = set()
    if re.search(
        r"\b(?:work|job|occupation|business|office|desk|student|retired|"
        r"homemaker|unemployed|developer|engineer|teacher|nurse|consultant|"
        r"manager|driver|9\s*-?\s*5)\b",
        text,
    ):
        found.add("work")
    if re.search(
        r"\b(?:exercise|workout|gym|walking|walks|running|cycling|swimming|"
        r"active|sedentary|activity routine|times? (?:a|per) week|days? (?:a|per) week)\b",
        text,
    ):
        found.add("activity")
    if re.search(r"\b(?:smok|smoker|tobacco)\w*\b", text):
        found.add("smoking")
    if re.search(r"\b(?:alcohol|drink|drinks|drinking)\b", text):
        found.add("alcohol")
    return found


def missing_lifestyle_components(value: str | None) -> list[str]:
    found = lifestyle_components(value)
    return [
        field for component, field in LIFESTYLE_COMPONENT_FIELDS.items()
        if component not in found
    ]


def merge_lifestyle_answer(existing: str | None, user_input: str | None) -> str:
    """Merge explicit smoking, alcohol, and exercise facts into one field."""

    text = " ".join(str(user_input or "").lower().replace("’", "'").split())
    facts: list[str] = []

    if re.search(
        r"\b(?:i (?:work(?! out)|am working)|my (?:work|job)|work involves|job involves|"
        r"work (?:in|at|for)|business|office|desk job|student|retired|homemaker|"
        r"unemployed|9\s*-?\s*5)\b",
        text,
    ):
        facts.append(f"Work: {str(user_input).strip()}")

    if re.search(
        r"\b(?:exercise|workout|gym|i walk|walking only|running|cycling|swimming|"
        r"no other exercise|no exercise|times? (?:a|per) week|days? (?:a|per) week)\b",
        text,
    ):
        facts.append(f"Activity/exercise: {str(user_input).strip()}")

    does_not_smoke = bool(
        re.search(r"\b(?:i )?(?:do not|don't|dont|never) smoke\b", text)
        or re.search(r"\bnon[- ]?smoker\b", text)
    )
    if does_not_smoke:
        facts.append("Smoking: Does not smoke")
    elif re.search(
        r"\b(?:i (?:currently |occasionally |regularly )?|yes[ ,:-]*)smoke\b",
        text,
    ):
        facts.append("Smoking: Smokes")

    does_not_drink = bool(
        re.search(
            r"\b(?:i )?(?:do not|don't|dont|never) drink(?: alcohol)?\b",
            text,
        )
        or "no alcohol" in text
    )
    if does_not_drink:
        facts.append("Alcohol: Does not drink alcohol")
    elif re.search(
        r"\b(?:i (?:rarely |occasionally |regularly )?|yes[ ,:-]*)drink(?: alcohol)?\b",
        text,
    ):
        facts.append("Alcohol: Drinks alcohol")

    if re.search(r"\bi (?:only )?walk\b", text) or "walking only" in text:
        facts.append("Activity/exercise: Walks for exercise")
    if any(
        phrase in text
        for phrase in (
            "don't have any other exercise", "dont have any other exercise",
            "do not have any other exercise", "no other exercise",
            "no exercise routine", "don't exercise", "dont exercise",
        )
    ):
        facts.append("Activity/exercise: No other regular exercise routine")

    current = str(existing or "").strip()
    if not facts:
        return current

    merged_parts = [part.strip() for part in current.split(";") if part.strip()]
    normalized_existing = current.lower()
    for fact in facts:
        if fact.lower() not in normalized_existing:
            merged_parts.append(fact)
            normalized_existing += f"; {fact.lower()}"
    return "; ".join(merged_parts)
