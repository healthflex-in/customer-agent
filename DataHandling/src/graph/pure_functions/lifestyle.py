"""Deterministic extraction for common lifestyle statements."""

from __future__ import annotations

import re
import ast
import json


LIFESTYLE_COMPONENT_FIELDS = {
    "work": "Current Lifestyle — Work",
    "activity": "Current Lifestyle — Activity or Exercise",
    "smoking": "Current Lifestyle — Smoking",
    "alcohol": "Current Lifestyle — Alcohol",
}


def format_lifestyle_value(value) -> str:
    """Normalize structured or serialized lifestyle data to readable text."""
    parsed = value
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("{") and candidate.endswith("}"):
            for loader in (json.loads, ast.literal_eval):
                try:
                    parsed = loader(candidate)
                    break
                except (ValueError, SyntaxError, TypeError, json.JSONDecodeError):
                    continue
        else:
            return candidate
    if not isinstance(parsed, dict):
        return str(value or "").strip()
    labels = {
        "work": "Work",
        "activity/exercise": "Activity/exercise",
        "activity": "Activity/exercise",
        "exercise": "Activity/exercise",
        "smoking": "Smoking",
        "alcohol": "Alcohol",
    }
    parts = []
    for key, item in parsed.items():
        if item is None or not str(item).strip():
            continue
        label = labels.get(str(key).strip().lower(), str(key).strip())
        parts.append(f"{label}: {str(item).strip()}")
    return "; ".join(parts)


def lifestyle_components(value: str | None) -> set[str]:
    """Return the explicitly documented lifestyle subtopics."""

    text = " ".join(format_lifestyle_value(value).lower().replace("’", "'").split())
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


def merge_lifestyle_answer(
    existing: str | None,
    user_input: str | None,
    last_question: str | None = None,
) -> str:
    """Merge explicit smoking, alcohol, and exercise facts into one field."""

    text = " ".join(str(user_input or "").lower().replace("’", "'").split())
    question = " ".join(str(last_question or "").lower().split())
    facts: dict[str, str] = {}

    if re.search(
        r"\b(?:i (?:work(?! out)|am working)|my (?:work|job)|work involves|job involves|"
        r"work (?:in|at|for)|business|office|desk job|student|retired|homemaker|"
        r"unemployed|9\s*-?\s*5)\b",
        text,
    ):
        facts["work"] = f"Work: {str(user_input).strip()}"

    pain_context = bool(re.search(
        r"\b(?:pain|worse|aggravat|relief|reliev|massage|hurt|increases?|decreases?)\b",
        text,
    ))
    lifestyle_question = bool(re.search(
        r"\b(?:exercise routine|usual activity|workout|gym|physically active)\b",
        question,
    ))
    explicit_activity = bool(re.search(
        r"\b(?:exercise|work\s*out|gym|walking only|running|cycling|swimming|yoga|zumba|"
        r"no other exercise|no exercise|times? (?:a|per) week|days? (?:a|per) week)\b",
        text,
    ))
    walking_as_routine = bool(
        re.search(r"\bi (?:only )?walk\b", text)
        and not pain_context
        and ("only walk" in text or lifestyle_question)
    )
    if explicit_activity or walking_as_routine:
        facts["activity"] = f"Activity/exercise: {str(user_input).strip()}"

    does_not_smoke = bool(
        re.search(r"\b(?:i )?(?:do not|don't|dont|never) smoke\b", text)
        or re.search(r"\bnon[- ]?smoker\b", text)
    )
    if does_not_smoke:
        facts["smoking"] = "Smoking: Does not smoke"
    elif re.search(
        r"\b(?:i (?:currently |occasionally |regularly )?|yes[ ,:-]*)smoke\b",
        text,
    ):
        facts["smoking"] = "Smoking: Smokes"

    does_not_drink = bool(
        re.search(
            r"\b(?:i )?(?:do not|don't|dont|never) drink(?: alcohol)?\b",
            text,
        )
        or "no alcohol" in text
    )
    if does_not_drink:
        facts["alcohol"] = "Alcohol: Does not drink alcohol"
    elif re.search(
        r"\b(?:i (?:rarely |occasionally |regularly )?|yes[ ,:-]*)drink(?: alcohol)?\b",
        text,
    ):
        facts["alcohol"] = "Alcohol: Drinks alcohol"

    walks_for_exercise = walking_as_routine or "walking only" in text
    no_other_exercise = any(
        phrase in text
        for phrase in (
            "don't have any other exercise", "dont have any other exercise",
            "do not have any other exercise", "no other exercise",
            "no exercise routine", "don't exercise", "dont exercise",
        )
    )
    if walks_for_exercise and no_other_exercise:
        facts["activity"] = (
            "Activity/exercise: Walks for exercise. No other regular "
            "exercise routine"
        )
    elif walks_for_exercise:
        facts["activity"] = "Activity/exercise: Walks for exercise"
    elif no_other_exercise:
        facts["activity"] = "Activity/exercise: No other regular exercise routine"

    current = format_lifestyle_value(existing)
    if not facts:
        return current

    merged_parts = [part.strip() for part in current.split(";") if part.strip()]
    prefixes = {
        "work": ("work:",),
        "activity": ("activity:", "activity/exercise:", "exercise:"),
        "smoking": ("smoking:",),
        "alcohol": ("alcohol:",),
    }
    for component, fact in facts.items():
        merged_parts = [
            part for part in merged_parts
            if not part.lower().startswith(prefixes[component])
        ]
        merged_parts.append(fact)
    return "; ".join(merged_parts)
