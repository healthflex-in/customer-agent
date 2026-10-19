"""Store lifestyle answers on the question that was asked.

A reply is not scanned for job titles or exercise names. Commas, semicolons,
and line breaks separate answers. On the activity-and-habits question, a
habit answer may also follow the activity in the same line. The clause is
written only when that question belongs to work, activity, smoking, or
drinking. An answer to relief, reports, or any other question stays out.
"""

from __future__ import annotations

import ast
import json
import re

from src.graph.pure_functions.question_plan import (
    LIFESTYLE_COMPONENT_QUESTIONS,
    REQUIRED_FIELD_QUESTIONS,
)


LIFESTYLE_COMPONENT_FIELDS = {
    "work": "Current Lifestyle — Work",
    "activity": "Current Lifestyle — Activity or Exercise",
    "smoking": "Current Lifestyle — Smoking",
    "alcohol": "Current Lifestyle — Alcohol",
}

_LABELS = {
    "work": "Work",
    "activity": "Activity/exercise",
    "smoking": "Smoking",
    "alcohol": "Alcohol",
}
_PREFIXES = {
    "work": ("work:", "occupation:"),
    "activity": ("activity:", "activity/exercise:", "exercise:"),
    "smoking": ("smoking:",),
    "alcohol": ("alcohol:",),
}
_LABEL_TO_COMPONENT = {
    "work": "work",
    "occupation": "work",
    "activity": "activity",
    "activity/exercise": "activity",
    "exercise": "activity",
    "smoking": "smoking",
    "alcohol": "alcohol",
}


_HABITS = ("smoking", "alcohol")
_COMBINED = "combined"
_SMOKING_WORD = re.compile(
    r"\b(?:smok(?:e|es|ing|er)?|tobacco|cigarettes?|vap(?:e|ing))\b",
    re.I,
)
_DRINKING_WORD = re.compile(
    r"\b(?:drinks?|drinking|drank|alcohol|alcoholic|beer|wine)\b",
    re.I,
)
_BARE_POLAR_REPLY = re.compile(
    r"^(?:yes|yeah|yep|yup|no|nope|nah|never|not really)"
    r"(?:\s+(?:i\s+)?(?:do|don'?t|dont|dnt|do not|never))?[\s.!]*$",
    re.I,
)
# A habit answer can follow an activity with only a space or a line break.
# Longer alternatives come first so "nope" is not read as "no".
_HABIT_BOUNDARY = re.compile(
    r"(?:^|(?<=\s))(?:"
    r"not really|yeah|yep|yup|yes|nope|nah|never|no"
    r"|(?:i\s+)?(?:do\s+not|don'?t|dont|dnt|never)\s+(?:smok|drink|alcohol|tobacco)"
    r"|(?:i\s+)?(?:"
    r"smok(?:e|es|ing|er)?|tobacco|cigarettes?|vap(?:e|ing)"
    r"|drinks?|drinking|drank|alcohol|alcoholic|beer|wine"
    r")"
    r")\b",
    re.I,
)


def _normalize(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def _combined_question() -> str:
    return _normalize(REQUIRED_FIELD_QUESTIONS["Current Lifestyle"])


def _known_questions() -> dict[str, tuple[str, ...]]:
    """Questions this interview asks, and the lifestyle fields each one fills."""

    combined = _combined_question()
    habit_clause = combined.split("? ", 1)[-1]
    return {
        combined: (_COMBINED,),
        _normalize(LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Work"]): (
            "work",
        ),
        _normalize(
            LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Activity or Exercise"]
        ): ("activity",),
        _normalize(LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Smoking"]): (
            "smoking",
        ),
        _normalize(LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Alcohol"]): (
            "alcohol",
        ),
        habit_clause: ("smoking", "alcohol"),
    }


def _questions_asked(question: str | None) -> list[str]:
    bullets = []
    for line in str(question or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            bullets.append(stripped[2:].strip())
    if bullets:
        return bullets
    text = str(question or "").strip()
    return [text] if text else []


def _clauses(user_input: str | None) -> list[str]:
    raw = str(user_input or "").strip()
    if not raw or raw.endswith("?"):
        return []
    clauses = []
    for line in re.split(r"[\r\n]+", raw):
        line = " ".join(line.split())
        if not line or line.endswith("?"):
            continue
        for part in re.split(r"\s*[,;]\s*", line):
            piece = part.strip()
            if piece and not piece.endswith("?"):
                clauses.append(piece)
    return clauses


def _fields_for(asked: str, *, allow_embedded: bool) -> tuple[str, ...] | None:
    """Lifestyle fields for one asked question.

    A lone question may carry a lead-in (for example a resume greeting), so it
    is matched by the longest known question that ends it as whole sentences.
    """

    known = _known_questions()
    normalized = _normalize(asked)
    if normalized in known:
        return known[normalized]
    if not allow_embedded:
        return None
    for text in sorted(known, key=len, reverse=True):
        if not normalized.endswith(text):
            continue
        lead_in = normalized[: -len(text)].rstrip()
        if lead_in.endswith((".", "?", "!", ":")):
            return known[text]
    return None


def _shared_habit_answer(clause: str) -> dict[str, str]:
    """Fill the habit the reply names.

    "Do you smoke or drink?" is one sentence about two fields. A bare yes or
    no names neither, so it answers both. Naming only drinking, or only
    smoking, leaves the other field to be asked on its own.
    """

    text = clause.strip()
    names_smoking = bool(_SMOKING_WORD.search(text))
    names_alcohol = bool(_DRINKING_WORD.search(text))
    if names_smoking and not names_alcohol:
        return {"smoking": text}
    if names_alcohol and not names_smoking:
        return {"alcohol": text}
    return {"smoking": text, "alcohol": text}


def _activity_and_habit(clause: str) -> tuple[str, str] | None:
    """Split "cardio no i dont drink" into the activity and the habit answer."""

    match = _HABIT_BOUNDARY.search(clause)
    if not match or match.start() == 0:
        return None
    activity = clause[: match.start()].strip(" \t,;.-")
    activity = re.sub(r"\s+\band$", "", activity, flags=re.I).strip()
    habit = clause[match.start() :].strip()
    if not activity or not habit:
        return None
    return activity, habit


def _combined_answers(clauses: list[str]) -> dict[str, str]:
    """Map a reply to "activity? smoke or drink?" by clause position.

    One clause is the habit answer when it is a bare yes/no or names a habit;
    otherwise it names the activity. A habit answer written after the activity
    on the same line is separated. Two clauses are activity, then the habit
    answer. Three are work, activity, then the habit answer. A habit answer
    that names only one habit fills only that field. Anything longer is not
    guessed.
    """

    if len(clauses) == 1:
        clause = clauses[0]
        peeled = _activity_and_habit(clause)
        if peeled:
            activity, habit = peeled
            return {"activity": activity, **_shared_habit_answer(habit)}
        if _BARE_POLAR_REPLY.match(clause) or _HABIT_BOUNDARY.match(clause):
            return _shared_habit_answer(clause)
        return {"activity": clause}
    if len(clauses) == 2:
        return {"activity": clauses[0], **_shared_habit_answer(clauses[1])}
    if len(clauses) == 3:
        return {
            "work": clauses[0],
            "activity": clauses[1],
            **_shared_habit_answer(clauses[2]),
        }
    return {}


def _width(fields: tuple[str, ...] | None) -> int:
    return 2 if fields == (_COMBINED,) else 1


def _mapped_answers(question: str | None, user_input: str | None) -> dict[str, str]:
    """Pair each reply clause with the lifestyle question in that position."""

    questions = _questions_asked(question)
    clauses = _clauses(user_input)
    if not questions or not clauses:
        return {}

    if len(questions) == 1:
        fields = _fields_for(questions[0], allow_embedded=True)
        if fields == (_COMBINED,):
            return _combined_answers(clauses)
        if fields == _HABITS:
            return _shared_habit_answer(" ".join(clauses))
        if fields and len(fields) == 1:
            return {fields[0]: " ".join(clauses)}
        return {}

    asked_fields = [_fields_for(asked, allow_embedded=False) for asked in questions]
    if sum(_width(fields) for fields in asked_fields) != len(clauses):
        return {}

    assigned: dict[str, str] = {}
    position = 0
    for fields in asked_fields:
        width = _width(fields)
        own = clauses[position:position + width]
        position += width
        if not fields:
            continue
        if fields == (_COMBINED,):
            assigned.update(_combined_answers(own))
        elif fields == _HABITS:
            assigned.update(_shared_habit_answer(own[0]))
        else:
            assigned[fields[0]] = own[0]
    return assigned


def _labelled_value(value: str | None, component: str) -> str | None:
    prefix = _PREFIXES[component]
    for part in format_lifestyle_value(value).split(";"):
        piece = part.strip()
        lowered = piece.lower()
        if lowered.startswith(prefix):
            return piece.split(":", 1)[1].strip()
    return None


def _replace_component(parts: list[str], component: str, value: str) -> list[str]:
    prefixes = _PREFIXES[component]
    kept = [part for part in parts if not part.lower().startswith(prefixes)]
    kept.append(f"{_LABELS[component]}: {value}")
    return kept


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
            labelled_parts = []
            recognized_label = False
            for part in candidate.split(";"):
                piece = part.strip()
                if ":" not in piece:
                    if piece:
                        labelled_parts.append(piece)
                    continue
                label, item = piece.split(":", 1)
                if label.strip().lower() not in _LABEL_TO_COMPONENT:
                    labelled_parts.append(piece)
                    continue
                recognized_label = True
                cleaned = " ".join(item.strip().split())
                if cleaned:
                    labelled_parts.append(f"{label.strip()}: {cleaned}")
            if recognized_label:
                return "; ".join(part for part in labelled_parts if part)
            return candidate
    if not isinstance(parsed, dict):
        return str(value or "").strip()
    parts = []
    for key, item in parsed.items():
        if item is None or not str(item).strip():
            continue
        component = _LABEL_TO_COMPONENT.get(str(key).strip().lower())
        label = _LABELS.get(component, str(key).strip()) if component else str(key).strip()
        cleaned = " ".join(str(item).strip().split())
        if cleaned:
            parts.append(f"{label}: {cleaned}")
    return "; ".join(parts)


def lifestyle_components(value: str | None) -> set[str]:
    """Return lifestyle fields that already have a stored answer."""

    found: set[str] = set()
    for part in format_lifestyle_value(value).split(";"):
        if ":" not in part:
            continue
        label = part.split(":", 1)[0].strip().lower()
        component = _LABEL_TO_COMPONENT.get(label)
        if component:
            found.add(component)
    return found


def missing_lifestyle_components(value: str | None) -> list[str]:
    found = lifestyle_components(value)
    return [
        field for component, field in LIFESTYLE_COMPONENT_FIELDS.items()
        if component not in found
    ]


def _habit_targets(question: str | None) -> set[str]:
    """Habit fields the question itself asked, before the reply is read."""

    questions = _questions_asked(question)
    targets: set[str] = set()
    allow_embedded = len(questions) == 1
    for asked in questions:
        fields = _fields_for(asked, allow_embedded=allow_embedded)
        if fields in ((_COMBINED,), _HABITS):
            targets.update(("smoking", "alcohol"))
        elif fields and fields[0] in ("smoking", "alcohol"):
            targets.add(fields[0])
    return targets


def merge_lifestyle_answer(
    existing: str | None,
    user_input: str | None,
    last_question: str | None = None,
    model_lifestyle: str | None = None,
) -> str:
    """Add the reply to the lifestyle field whose question was just asked."""

    current = format_lifestyle_value(existing)
    parts = [part.strip() for part in current.split(";") if part.strip()]
    assigned = _mapped_answers(last_question, user_input)
    targets = _habit_targets(last_question)
    if len(targets) == 1:
        only = next(iter(targets))
        other = "alcohol" if only == "smoking" else "smoking"
        if only in assigned and other not in assigned:
            other_value = _labelled_value(model_lifestyle, other)
            if other_value:
                assigned[other] = other_value
    if not assigned:
        return current
    for component, value in assigned.items():
        parts = _replace_component(parts, component, value)
    return "; ".join(parts)


def has_explicit_lifestyle_fact(user_input: str | None) -> bool:
    """Lifestyle facts are recognised from the question, not from reply wording."""

    return False


def update_lifestyle_component(
    existing: str | None,
    component_name: str | None,
    new_value: str | None,
    source_text: str | None = None,
) -> str | None:
    """Write one named lifestyle component. The caller already chose the field."""

    del source_text
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
    value = str(new_value or "").strip()
    value = re.sub(
        r"^(?:work|occupation|activity(?:/exercise)?|exercise|smoking|alcohol)\s*:\s*",
        "",
        value,
        flags=re.I,
    ).strip()
    if not value:
        return None
    current = format_lifestyle_value(existing)
    parts = [part.strip() for part in current.split(";") if part.strip()]
    return "; ".join(_replace_component(parts, component, value))


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
    current = format_lifestyle_value(existing)
    parts = [part.strip() for part in current.split(";") if part.strip()]
    remaining = [
        part for part in parts
        if not part.lower().startswith(_PREFIXES[component])
    ]
    result = "; ".join(remaining)
    return result if result != current else None
