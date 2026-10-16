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

_HEALTH_DENIAL_AS_WORK = re.compile(
    r"\b(?:no|none|not\w*|never)\b.{0,30}"
    r"\b(?:health|cond\w*|cont\w*|ill\w*|surg\w*|fract\w*)\b",
    re.I,
)


def _clean_lifestyle_component(label: str, value: object) -> str:
    """Remove cross-field fragments from one labelled lifestyle component."""
    cleaned = " ".join(str(value or "").strip().split())
    key = label.strip().lower()
    if key in {"work", "occupation"} and _HEALTH_DENIAL_AS_WORK.search(cleaned):
        return ""
    if key in {"activity", "activity/exercise", "exercise"}:
        clauses = _lifestyle_clauses(cleaned)
        activity_clause = next(
            (
                clause for clause in clauses
                if re.search(
                    r"\b(?:exercise|work\s*out|gym|walk|run|cycling|swimming|"
                    r"yoga|zumba|times? (?:a|per) week|days? (?:a|per) week)\b",
                    clause,
                    re.I,
                )
            ),
            "",
        )
        if activity_clause:
            return activity_clause
    return cleaned


def _lifestyle_clauses(user_input: str | None) -> list[str]:
    """Split a compact answer without mixing one lifestyle fact into another."""
    raw = " ".join(str(user_input or "").strip().split())
    if not raw:
        return []
    parts = re.split(
        r"\s*[,;]\s*|\s+\band\b\s+(?=(?:i\s+)?(?:(?:yes|no|never|don't|dont|dnt|"
        r"do not)\s+)?(?:work|job|"
        r"occupation|exercise|work\s*out|walk|run|gym|smok|drink|alcohol|"
        r"go(?:es|ing)?\s+to\s+(?:the\s+)?gym))",
        raw,
        flags=re.I,
    )
    # Voice transcription frequently omits punctuation: "I work in a company
    # no drink" or "I exercise three days a week I smoke". Insert a boundary
    # before an explicit habit answer.  The yes/no qualifier is optional.
    expanded: list[str] = []
    for part in parts:
        expanded.extend(
            re.split(
                r"\s+(?=(?:i\s+)?(?:(?:yes|no|never|don't|dont|dnt|do not)\s+)?"
                r"(?:smok|drink|alcohol)\w*\b)",
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
            # Canonical stored values are semicolon-delimited labelled
            # components. Re-clean them on read so previously contaminated
            # records no longer count as completed lifestyle answers.
            labelled_parts = []
            recognized_label = False
            for part in candidate.split(";"):
                if ":" not in part:
                    labelled_parts.append(part.strip())
                    continue
                label, item = part.split(":", 1)
                if label.strip().lower() not in {
                    "work", "occupation", "activity", "activity/exercise",
                    "exercise", "smoking", "alcohol",
                }:
                    labelled_parts.append(part.strip())
                    continue
                recognized_label = True
                cleaned = _clean_lifestyle_component(label, item)
                if cleaned:
                    labelled_parts.append(f"{label.strip()}: {cleaned}")
            if recognized_label:
                return "; ".join(part for part in labelled_parts if part)
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
        cleaned = _clean_lifestyle_component(label, item)
        if cleaned:
            parts.append(f"{label}: {cleaned}")
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
    # Normalize common typed/transcribed contractions for matching only. The
    # original patient wording is still used for stored occupation/activity.
    text = re.sub(r"\bdnt\b", "don't", text)
    text = re.sub(r"\bdo\s+n\s*'?t\b", "don't", text)
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

    # A direct answer to an occupation question can be just a job title
    # ("accountant", "painter") and therefore contain no work keyword.  The
    # question supplies the field context; capture the answer as Work while
    # excluding clauses that clearly answer a different lifestyle component.
    asks_work = bool(re.search(
        r"\b(?:what is your work|what do you do for work|work or usual "
        r"occupation|your occupation|your job)\b",
        question,
    ))
    # In a batched question a short answer may belong to a neighbouring topic.
    # For example, "no other conditions" answers the health-history question,
    # not occupation.  Only use the bare-answer fallback when no other clinical
    # topic was asked; explicit work statements above remain safe in any batch.
    asks_other_clinical_topic = bool(re.search(
        r"\b(?:health conditions?|medical conditions?|surger(?:y|ies)|fractures?|"
        r"x-?rays?|mri|ct scans?|blood reports?|seen a doctor|physiotherapist|"
        r"hospital|diagnosis|treatment|pain|severity|makes? it worse|"
        r"gives? relief|goals?|expectations?|how long|start(?:ed)? suddenly|"
        r"start(?:ed)? gradually)\b",
        question,
    ))
    if asks_work and not asks_other_clinical_topic and "work" not in facts:
        other_component = re.compile(
            r"\b(?:smok|tobacco|drink|alcohol|exercise|work\s*out|gym|"
            r"walking|running|cycling|swimming|yoga|zumba)\w*\b",
            re.I,
        )
        work_candidates = [
            clause for clause in _lifestyle_clauses(user_input)
            if not other_component.search(clause)
            and not _HEALTH_DENIAL_AS_WORK.search(clause)
            and not clause.rstrip().endswith("?")
        ]
        if work_candidates:
            facts["work"] = f"Work: {work_candidates[0]}"

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

    asks_smoking = bool(re.search(r"\b(?:smok\w*|tobacco)\b", question))
    asks_alcohol = bool(re.search(r"\b(?:drink(?:ing)? alcohol|alcohol)\b", question))
    short_negative = text.strip(" .,!?-") in {
        "no", "nope", "never", "none", "not at all", "nah",
    }
    shared_habit_negation = bool(
        re.search(
            r"\b(?:i\s+)?(?:do not|don't|dont|never|no)\s+"
            r"(?:smoke|use tobacco)\s+(?:or|and)\s+"
            r"(?:drink|consume)(?: alcohol)?\b",
            text,
        )
        or re.search(
            r"\b(?:i\s+)?(?:do not|don't|dont|never|no)\s+"
            r"(?:drink|consume)(?: alcohol)?\s+(?:or|and)\s+"
            r"(?:smoke|use tobacco)\b",
            text,
        )
    )

    does_not_smoke = bool(
        shared_habit_negation
        or (short_negative and asks_smoking)
        or re.search(r"\b(?:i )?(?:do not|don't|dont|never|no) smoke\b", text)
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
        shared_habit_negation
        or (short_negative and asks_alcohol)
        or re.search(
            r"\b(?:i )?(?:do not|don't|dont|never|no) drink(?: alcohol)?\b",
            text,
        )
        or "no alcohol" in text
        or "not drink" in text
    )
    if does_not_drink:
        facts["alcohol"] = "Alcohol: Does not drink alcohol"
    elif re.search(
        r"\b(?:i\s+(?:do\s+|rarely\s+|occasionally\s+|regularly\s+)?|"
        r"yes[ ,:-]*(?:i\s+)?)drink(?:s)?(?: alcohol)?\b",
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
    text = re.sub(r"\bdnt\b", "don't", text)
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
