"""Store supported lifestyle answers on the question that was asked.

Work and activity/exercise are the only interview components. Existing
semicolon-delimited parts with any other labels or prose remain opaque and are
preserved when either supported component is updated.
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
}

_LABELS = {
    "work": "Work",
    "activity": "Activity/exercise",
}
_PREFIXES = {
    "work": ("work:", "occupation:"),
    "activity": ("activity:", "activity/exercise:", "exercise:"),
}
_LABEL_TO_COMPONENT = {
    "work": "work",
    "occupation": "work",
    "activity": "activity",
    "activity/exercise": "activity",
    "exercise": "activity",
}


def _normalize(text: str | None) -> str:
    return " ".join(str(text or "").lower().split())


def _known_questions() -> dict[str, str]:
    """Return the interview questions that fill supported lifestyle fields."""

    return {
        _normalize(REQUIRED_FIELD_QUESTIONS["Current Lifestyle"]): "activity",
        _normalize(
            LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Work"]
        ): "work",
        _normalize(
            LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Activity or Exercise"]
        ): "activity",
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


def _field_for(asked: str, *, allow_embedded: bool) -> str | None:
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


def _mapped_answers(question: str | None, user_input: str | None) -> dict[str, str]:
    """Pair reply clauses only with supported lifestyle questions by position."""

    questions = _questions_asked(question)
    clauses = _clauses(user_input)
    if not questions or not clauses:
        return {}

    if len(questions) == 1:
        field = _field_for(questions[0], allow_embedded=True)
        if field is None:
            return {}
        return {field: " ".join(clauses)}

    fields = [_field_for(asked, allow_embedded=False) for asked in questions]
    if len(fields) != len(clauses):
        return {}
    return {
        field: clause
        for field, clause in zip(fields, clauses)
        if field is not None
    }


def _replace_component(parts: list[str], component: str, value: str) -> list[str]:
    prefixes = _PREFIXES[component]
    kept = [part for part in parts if not part.lower().startswith(prefixes)]
    kept.append(f"{_LABELS[component]}: {value}")
    return kept


def format_lifestyle_value(value) -> str:
    """Normalize structured lifestyle data without interpreting unknown parts."""

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
    parts = []
    for key, item in parsed.items():
        if item is None or not str(item).strip():
            continue
        component = _LABEL_TO_COMPONENT.get(str(key).strip().lower())
        label = _LABELS.get(component, str(key).strip()) if component else str(key).strip()
        parts.append(f"{label}: {' '.join(str(item).strip().split())}")
    return "; ".join(parts)


def lifestyle_components(value: str | None) -> set[str]:
    """Return supported lifestyle components that already have an answer."""

    found: set[str] = set()
    for part in format_lifestyle_value(value).split(";"):
        if ":" not in part:
            continue
        component = _LABEL_TO_COMPONENT.get(part.split(":", 1)[0].strip().lower())
        if component:
            found.add(component)
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
    model_lifestyle: str | None = None,
) -> str:
    """Update only the supported component whose question was just asked."""

    del model_lifestyle
    current = format_lifestyle_value(existing)
    parts = [part.strip() for part in current.split(";") if part.strip()]
    assigned = _mapped_answers(last_question, user_input)
    for component, value in assigned.items():
        parts = _replace_component(parts, component, value)
    return "; ".join(parts)


def has_explicit_lifestyle_fact(user_input: str | None) -> bool:
    """Lifestyle facts are recognized from the question, not reply wording."""

    del user_input
    return False


def update_lifestyle_component(
    existing: str | None,
    component_name: str | None,
    new_value: str | None,
    source_text: str | None = None,
) -> str | None:
    """Write one supported lifestyle component selected by the caller."""

    del source_text
    aliases = {
        "work": "work",
        "occupation": "work",
        "activity": "activity",
        "activity/exercise": "activity",
        "exercise": "activity",
    }
    component = aliases.get(str(component_name or "").strip().lower())
    if component is None:
        return None
    value = re.sub(
        r"^(?:work|occupation|activity(?:/exercise)?|exercise)\s*:\s*",
        "",
        str(new_value or "").strip(),
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
    """Remove one supported component while preserving every unrelated part."""

    aliases = {
        "work": "work",
        "occupation": "work",
        "activity": "activity",
        "activity/exercise": "activity",
        "exercise": "activity",
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
