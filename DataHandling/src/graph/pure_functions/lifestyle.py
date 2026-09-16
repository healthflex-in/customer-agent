"""Deterministic extraction for common lifestyle statements."""

from __future__ import annotations

import re


def merge_lifestyle_answer(existing: str | None, user_input: str | None) -> str:
    """Merge explicit smoking, alcohol, and exercise facts into one field."""

    text = " ".join(str(user_input or "").lower().replace("’", "'").split())
    facts: list[str] = []

    does_not_smoke = bool(
        re.search(r"\b(?:i )?(?:do not|don't|dont|never) smoke\b", text)
        or re.search(r"\bnon[- ]?smoker\b", text)
    )
    if does_not_smoke:
        facts.append("Does not smoke")
    elif re.search(r"\bi (?:currently |occasionally |regularly )?smoke\b", text):
        facts.append("Smokes")

    does_not_drink = bool(
        re.search(
            r"\b(?:i )?(?:do not|don't|dont|never) drink(?: alcohol)?\b",
            text,
        )
        or "no alcohol" in text
    )
    if does_not_drink:
        facts.append("Does not drink alcohol")
    elif re.search(r"\bi (?:occasionally |regularly )?drink alcohol\b", text):
        facts.append("Drinks alcohol")

    if re.search(r"\bi (?:only )?walk\b", text) or "walking only" in text:
        facts.append("Walks for exercise")
    if any(
        phrase in text
        for phrase in (
            "don't have any other exercise", "dont have any other exercise",
            "do not have any other exercise", "no other exercise",
            "no exercise routine", "don't exercise", "dont exercise",
        )
    ):
        facts.append("No other regular exercise routine")

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
