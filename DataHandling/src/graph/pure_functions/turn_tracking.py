"""Privacy-safe form-diff and repeated-question telemetry."""

from __future__ import annotations

from src.graph.pure_functions.form_validation import get_all_missing_fields


def field_ids(values: dict[str, list[str]]) -> list[str]:
    return [
        f"{section}.{field}"
        for section, fields in values.items()
        for field in fields
    ]


def missing_field_ids(form: dict, form_sections: list[str]) -> list[str]:
    return field_ids(get_all_missing_fields(form, form_sections))


def updated_field_ids(before: dict, after: dict) -> list[str]:
    sections = list(dict.fromkeys([*before.keys(), *after.keys()]))
    changed: list[str] = []
    for section in sections:
        old_fields = before.get(section, {})
        new_fields = after.get(section, {})
        if not isinstance(old_fields, dict) or not isinstance(new_fields, dict):
            if old_fields != new_fields:
                changed.append(section)
            continue
        for field in dict.fromkeys([*old_fields.keys(), *new_fields.keys()]):
            if old_fields.get(field) != new_fields.get(field):
                changed.append(f"{section}.{field}")
    return changed


def update_loop_counters(
    client_state: dict,
    *,
    missing_ids: list[str],
    question_ids: list[str],
) -> tuple[int, int, bool]:
    """Update per-socket loop counters and signal bounded state recovery."""

    missing_signature = tuple(sorted(missing_ids))
    question_signature = tuple(question_ids)

    if not question_signature:
        client_state["same_question_count"] = 0
        return int(client_state.get("same_missing_field_count", 0)), 0, False

    same_missing = (
        int(client_state.get("same_missing_field_count", 0)) + 1
        if client_state.get("last_missing_field_set") == missing_signature
        else 1
    )
    same_question = (
        int(client_state.get("same_question_count", 0)) + 1
        if question_signature
        and client_state.get("last_question_field_ids") == question_signature
        else (0 if not question_signature else 1)
    )
    client_state["last_missing_field_set"] = missing_signature
    client_state["last_question_field_ids"] = question_signature
    client_state["same_missing_field_count"] = same_missing
    client_state["same_question_count"] = same_question
    return same_missing, same_question, same_missing >= 3 or same_question >= 3


def current_complaint_index(section: str) -> int:
    if section == "Pain Assessment":
        return 0
    if section.startswith("Additional Complaint "):
        try:
            return int(section.rsplit(" ", 1)[-1])
        except ValueError:
            return -1
    return -1
