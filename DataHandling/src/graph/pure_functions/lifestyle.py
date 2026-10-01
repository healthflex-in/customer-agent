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


def _lifestyle_clauses(user_input: str | None) -> list[str]:
    """Split a compact answer without mixing one lifestyle fact into another."""
    raw = " ".join(str(user_input or "").strip().split())
    if not raw:
        return []
    parts = re.split(
        r"\s*[,;]\s*|\s+\band\b\s+(?=(?:i\s+)?(?:(?:yes|no|never|don't|dont|"
        r"do not)\s+)?(?:work|job|"
        r"occupation|exercise|work\s*out|walk|run|gym|smok|drink|alcohol))",
        raw,
        flags=re.I,
    )
    # Voice transcription frequently omits punctuation: "I work in a company
    # no drink". Insert a boundary before an explicit habit answer.
    expanded: list[str] = []
    for part in parts:
        expanded.extend(
            re.split(
                r"\s+(?=(?:i\s+)?(?:yes|no|never|don't|dont|do not)\s+"
                r"(?:smok|drink|alcohol))",
                part,
                flags=re.I,
            )
        )
    return [part.strip(" .") for part in expanded if part.strip(" .")]


def _first_matching_clause(user_input: str | None, pattern: str) -> str:
    for clause in _lifestyle_clauses(user_input):
        if re.search(pattern, clause, re.I):
            return clause
    return str(user_input or "").strip()


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
        work_clause = _first_matching_clause(
            user_input,
            r"\b(?:work(?! out)|job|occupation|business|office|desk job|student|"
            r"retired|homemaker|unemployed|9\s*-?\s*5)\b",
        )
        facts["work"] = f"Work: {work_clause}"

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
        activity_clause = _first_matching_clause(
            user_input,
            r"\b(?:exercise|work\s*out|gym|walk|running|cycling|swimming|yoga|"
            r"zumba|times? (?:a|per) week|days? (?:a|per) week)\b",
        )
        facts["activity"] = f"Activity/exercise: {activity_clause}"

    does_not_smoke = bool(
        re.search(r"\b(?:i )?(?:do not|don't|dont|never|no) smoke\b", text)
        or re.search(r"\bnon[- ]?smoker\b", text)
    )
    if does_not_smoke:
        facts["smoking"] = "Smoking: Does not smoke"
    elif re.search(
        r"\b(?:i (?:do |currently |occasionally |regularly )?|yes[ ,:-]*)smoke\b",
        text,
    ):
        facts["smoking"] = "Smoking: Smokes"

    does_not_drink = bool(
        re.search(
            r"\b(?:i )?(?:do not|don't|dont|never|no) drink(?: alcohol)?\b",
            text,
        )
        or "no alcohol" in text
        or "not drink" in text
    )
    if does_not_drink:
        facts["alcohol"] = "Alcohol: Does not drink alcohol"
    elif re.search(
        r"\b(?:i (?:do |rarely |occasionally |regularly )?|yes[ ,:-]*)"
        r"drink(?:s)?(?: alcohol)?\b",
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


def has_explicit_lifestyle_fact(user_input: str | None) -> bool:
    """Return True only when the message contains an actual habit/work value."""
    text = " ".join(str(user_input or "").lower().replace("’", "'").split())
    return bool(re.search(
        r"\b(?:i\s+(?:do\s+|do not\s+|don't\s+|dont\s+|never\s+|"
        r"occasionally\s+|rarely\s+|regularly\s+)?(?:smoke|drink)|"
        r"(?:yes|no)\s+(?:smoke|drink|alcohol)|non[- ]?smoker|no alcohol|"
        r"i\s+(?:work(?!\s*out)|am working)|my\s+(?:work|job)|"
        r"i\s+(?:exercise|work\s*out|walk|run)|no exercise)\b",
        text,
    ))


def update_lifestyle_component(
    existing: str | None,
    component_name: str | None,
    new_value: str | None,
    source_text: str | None = None,
) -> str | None:
    """Apply an LLM-resolved lifestyle subfield to the combined schema field.

    ``Alcohol`` and ``Work`` are display components, not physical form fields.
    Correction models sometimes return them as nested fields, so resolve those
    aliases here rather than rejecting a correction that the patient stated
    clearly.
    """
    aliases = {
        "work": "work",
        "occupation": "work",
        "activity": "activity",
        "activity/exercise": "activity",
        "exercise": "activity",
        "smoking": "smoking",
        "smoke": "smoking",
        "alcohol": "alcohol",
        "drinking": "alcohol",
    }
    component = aliases.get(str(component_name or "").strip().lower())
    if component is None:
        return None

    evidence = " ".join(
        part.strip() for part in (str(source_text or ""), str(new_value or ""))
        if part.strip()
    )
    current = format_lifestyle_value(existing)

    if component in {"smoking", "alcohol"}:
        merged = merge_lifestyle_answer(current, evidence)
        if merged != current:
            return merged

    value = str(new_value or "").strip()
    value = re.sub(
        r"^(?:work|occupation|activity(?:/exercise)?|exercise|smoking|alcohol)\s*:\s*",
        "",
        value,
        flags=re.I,
    ).strip()
    if not value:
        return None

    labels = {
        "work": "Work",
        "activity": "Activity/exercise",
        "smoking": "Smoking",
        "alcohol": "Alcohol",
    }
    prefixes = {
        "work": ("work:",),
        "activity": ("activity:", "activity/exercise:", "exercise:"),
        "smoking": ("smoking:",),
        "alcohol": ("alcohol:",),
    }
    parts = [part.strip() for part in current.split(";") if part.strip()]
    parts = [
        part for part in parts
        if not part.lower().startswith(prefixes[component])
    ]
    parts.append(f"{labels[component]}: {value}")
    return "; ".join(parts)


def clear_lifestyle_component(
    existing: str | None, component_name: str | None
) -> str | None:
    """Remove one virtual lifestyle component while preserving the others."""
    aliases = {
        "work": "work",
        "occupation": "work",
        "activity": "activity",
        "exercise": "activity",
        "smoking": "smoking",
        "smoke": "smoking",
        "alcohol": "alcohol",
        "drinking": "alcohol",
    }
    component = aliases.get(str(component_name or "").strip().lower())
    if component is None:
        return None
    prefixes = {
        "work": ("work:",),
        "activity": ("activity:", "activity/exercise:", "exercise:"),
        "smoking": ("smoking:",),
        "alcohol": ("alcohol:",),
    }
    current = format_lifestyle_value(existing)
    parts = [part.strip() for part in current.split(";") if part.strip()]
    remaining = [
        part for part in parts
        if not part.lower().startswith(prefixes[component])
    ]
    result = "; ".join(remaining)
    return result if result != current else None
