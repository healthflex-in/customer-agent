"""Deterministic helpers for structured PROM submissions."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence


PROM_COMPLETION_MESSAGE = (
    "Thank you for completing the assessment! Your responses have been recorded."
)


def parse_structured_prom_answers(
    text: str,
    question_meta: Mapping[str, Any] | None,
    input_mode: str | None = None,
) -> Optional[list[str]]:
    """Return validated positional answers, or ``None`` for the AI path.

    A submission is considered structured only when the multi-answer UI's pipe
    delimiter is present and every answer can be validated against its question
    metadata. ``None`` means the caller must retain the existing free-form/voice
    interpretation path.
    """

    if input_mode != "structured_prom" or not isinstance(question_meta, Mapping):
        return None
    if question_meta.get("type") != "multi_answer" or "|" not in text:
        return None

    question_ids = question_meta.get("question_ids")
    if not isinstance(question_ids, Sequence) or isinstance(question_ids, (str, bytes)):
        return None
    if len(question_ids) < 2 or any(not isinstance(qid, str) or not qid for qid in question_ids):
        return None

    question_types = question_meta.get("question_types")
    question_options = question_meta.get("question_options")
    if not isinstance(question_types, (list, tuple)) or len(question_types) != len(question_ids):
        return None
    if not isinstance(question_options, (list, tuple)) or len(question_options) != len(question_ids):
        return None

    answers = [answer.strip() for answer in text.split("|")]
    if len(answers) != len(question_ids) or any(not answer for answer in answers):
        return None

    for index, answer in enumerate(answers):
        question_type = str(question_types[index] or "text").lower()
        options = question_options[index]

        if question_type in {"scale", "linear_scale"}:
            if not answer.isdigit() or not 0 <= int(answer) <= 10:
                return None
        elif question_type in {"boolean", "yes_no"}:
            allowed = options if isinstance(options, list) and options else ["Yes", "No"]
            if answer not in allowed:
                return None
        elif question_type in {"single_choice", "dropdown"}:
            if not isinstance(options, list) or answer not in options:
                return None
        elif question_type not in {"text", "free_text"}:
            return None

    return answers


def advance_tagged_turn(
    turns: Sequence[str], metas: Sequence[Mapping[str, Any]], next_index: int
) -> tuple[str, Optional[Mapping[str, Any]], int, bool]:
    """Select the next deterministic PROM response and advance its cursor."""

    if next_index < 0:
        raise ValueError("Tagged turn index cannot be negative")
    if next_index < len(turns):
        meta = metas[next_index] if next_index < len(metas) else None
        return turns[next_index], meta, next_index + 1, False
    return PROM_COMPLETION_MESSAGE, None, next_index, True
