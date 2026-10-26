"""Map one patient reply onto every form field it answers.

The model reads the reply and decides what it means: which fields it answers
and whether each was answered, denied, or left unsure. This module checks the
returned values against the field rules declared in the form definition and
writes accepted answers onto a copy of the form.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Callable

from app.observability.privacy import error_type
from src.forms.loader import load_form
from src.graph.pure_functions.form_validation import extract_json_from_response
from src.graph.pure_functions.lifestyle import (
    LIFESTYLE_COMPONENT_FIELDS,
    lifestyle_component_value,
    update_lifestyle_component,
)
from src.graph.pure_functions.question_plan import (
    LIFESTYLE_COMPONENT_QUESTIONS,
    REQUIRED_FIELD_QUESTIONS,
)


NOT_PROVIDED = "Not provided"
NOT_APPLICABLE = "Not applicable"
VISIT_CONTEXTS = ("specific_complaint", "general_assessment", "unknown")

_FORM = load_form("FRM-01")
_LIFESTYLE_FIELD = "Current Lifestyle"
_STATUSES = {"answered", "denied", "unsure"}
_COMPONENT_OF = {name: component for component, name in LIFESTYLE_COMPONENT_FIELDS.items()}
# Sections a general visit skips are the ones that describe a single complaint.
COMPLAINT_SECTIONS = _FORM.visit_types["general_assessment"].skip_sections

_PROMPT = """You map a patient's reply in a physiotherapy intake interview onto intake form fields.

Sage (the assistant) last said:
{last_question}

The patient replied:
{reply}

Current visit type: {visit_context}

Each field has a short key such as F3. Use only that key as field_id.

Fields still unanswered (key: section — field — what it asks):
{unanswered}

Fields already answered (key: section — field — saved value):
{answered}

Field rules:
{rules}

How to map:
- Read the reply by meaning, the way an experienced clinician would. Patients answer in any order, in one sentence or several, with typos and voice-to-text errors.
- Map every fact in the reply to the field it answers, including fields Sage has not asked about yet.
- "answered": the patient gave the information. value is a short, clear restatement of what the patient said. Never add facts, guesses, or commentary.
- For a field with fixed values, value is exactly one of those values, chosen by what the field means, and detail is a short restatement of what the patient said about it (empty when they said nothing more). Other fields leave detail empty.
- "denied": the patient said there is none. value is a short negative statement in the patient's terms.
- "unsure": the patient does not know or does not remember. value is a short statement saying so.
- A short reply such as "no", "none" or "nothing" answers each field Sage just asked about that it sensibly answers.
- "correction": true only for an already answered field, and only when the patient clearly changes that earlier answer; otherwise false.
- A question the patient asks Sage is not an answer. Leave out fields the reply does not address.
- reports.has_reports: true or false only when the reply says whether the patient has diagnostic reports, otherwise null. reports.wants_upload: false when they will not upload here, true when they want to upload now, otherwise null.
- complaints: every distinct health problem the patient reports in this reply, each as a short clinical label in the patient's terms. Symptoms that belong to one condition, such as pain spreading from one area into another, are one problem. Empty when the reply reports none.
- visit_context: "specific_complaint" when the patient has a pain, injury or condition, "general_assessment" for a check-up or wellness visit without a complaint, otherwise "unknown".

Return only JSON in this shape:
{{"visit_context": "unknown", "answers": [{{"field_id": "F1", "status": "answered", "value": "...", "detail": "", "correction": false}}], "reports": {{"has_reports": null, "wants_upload": null}}, "complaints": []}}"""

_FOCUS_PROMPT = """A patient in a physiotherapy intake interview reported these separate problems:
{options}

Sage asked which one they would like to focus on today. The patient replied:
{reply}

Return only JSON: {{"choice": <number of the chosen problem, or null when the reply does not clearly choose one>}}"""


@dataclass(frozen=True)
class _Slot:
    field_id: str
    section: str
    field: str
    question: str


@dataclass
class ReplyMapping:
    form: dict
    visit_context: str
    updated_field_ids: list[str] = field(default_factory=list)
    reports_intent: dict = field(default_factory=dict)
    complaints: list[str] = field(default_factory=list)


def _slots(form: dict, form_sections: list[str]) -> list[_Slot]:
    slots: list[_Slot] = []
    for section in form_sections:
        fields = form.get(section)
        if not isinstance(fields, dict):
            continue
        names = list(dict.fromkeys([*_FORM.sections.get(section, []), *fields]))
        complaint = str(fields.get("Primary Complaint") or "").strip()
        about = (
            f" (about the additional complaint: {complaint})"
            if section.startswith("Additional Complaint ") and complaint
            else ""
        )
        for name in names:
            if name == _LIFESTYLE_FIELD:
                for component_field in LIFESTYLE_COMPONENT_FIELDS.values():
                    slots.append(_Slot(
                        f"{section}.{component_field}", section, component_field,
                        LIFESTYLE_COMPONENT_QUESTIONS[component_field],
                    ))
                continue
            question = REQUIRED_FIELD_QUESTIONS.get(name) or _FORM.field_labels.get(name, name)
            slots.append(_Slot(f"{section}.{name}", section, name, question + about))
    return slots


def _rules_text() -> str:
    lines = []
    for name, rule in _FORM.field_rules.items():
        if rule.values:
            meaning = _FORM.field_labels.get(name)
            lines.append(
                f"- {name}: one of {', '.join(rule.values)}"
                + (f". It means: {meaning}" if meaning else "")
            )
        if rule.scale:
            lines.append(f"- {name}: a whole number from {rule.scale[0]} to {rule.scale[1]}")
        if rule.applies_when_answered:
            lines.append(
                f"- {name}: only applies when the patient answered "
                f"{rule.applies_when_answered}; leave it out otherwise"
            )
    return "\n".join(lines) or "(none)"


def _saved_value(form: dict, slot: _Slot) -> str:
    fields = form.get(slot.section) or {}
    if slot.field in _COMPONENT_OF:
        return lifestyle_component_value(fields.get(_LIFESTYLE_FIELD), _COMPONENT_OF[slot.field])
    return str(fields.get(slot.field) or "").strip()


def _is_answered(form: dict, slot: _Slot) -> bool:
    value = _saved_value(form, slot)
    return bool(value) and value != NOT_PROVIDED


def _write(form: dict, slot: _Slot, value: str) -> None:
    fields = form.setdefault(slot.section, {})
    if slot.field in _COMPONENT_OF:
        lifestyle = update_lifestyle_component(
            fields.get(_LIFESTYLE_FIELD), _COMPONENT_OF[slot.field], value
        )
        if lifestyle is not None:
            fields[_LIFESTYLE_FIELD] = lifestyle
        return
    fields[slot.field] = value


def _settle_dependents(form: dict, section: str, field_name: str, answered: bool) -> None:
    """Dependents apply only to an answered field; otherwise they are not applicable."""

    fields = form.get(section)
    if not isinstance(fields, dict):
        return
    for dependent in _FORM.dependents_of(field_name):
        if not answered:
            fields[dependent] = NOT_APPLICABLE
        elif fields.get(dependent) == NOT_APPLICABLE:
            fields[dependent] = ""


def record_not_provided(form: dict, field_ids: list[str]) -> dict:
    """Return a copy of ``form`` with each field id saved as Not provided."""

    updated = copy.deepcopy(form)
    for field_id in field_ids:
        section, name = field_id.split(".", 1)
        _write(updated, _Slot(field_id, section, name, ""), NOT_PROVIDED)
        _settle_dependents(updated, section, name, answered=False)
    return updated


def apply_visit_type(form: dict, previous: str, current: str) -> dict:
    """Mark the sections a visit type skips as not applicable.

    Sections skipped by the previous visit type are reopened when the new
    type needs them, so a complaint revealed later is asked about in full.
    """

    updated = copy.deepcopy(form)
    skipped = _FORM.visit_types.get(current)
    skipped = skipped.skip_sections if skipped else frozenset()
    reopened = _FORM.visit_types.get(previous)
    reopened = (reopened.skip_sections if reopened else frozenset()) - skipped
    for section in reopened:
        fields = updated.get(section)
        if isinstance(fields, dict):
            for name, value in fields.items():
                if value == NOT_APPLICABLE:
                    fields[name] = ""
    for section in skipped:
        fields = updated.get(section)
        if isinstance(fields, dict):
            for name, value in fields.items():
                if not str(value or "").strip():
                    fields[name] = NOT_APPLICABLE
    return updated


def _next_visit_context(current: str, proposed: str) -> str:
    """A visit type is set once, and only a revealed complaint may change it."""

    if current == "unknown":
        return proposed
    if current == "general_assessment" and proposed == "specific_complaint":
        return proposed
    return current


def _accepted_value(status: str, value: str, detail: str, slot: _Slot) -> str | None:
    """Return the value to save, or None when it breaks the field's rule."""

    if not value:
        return None
    if status in ("denied", "unsure"):
        return value
    rule = _FORM.rule(slot.field)
    if rule.scale:
        low, high = rule.scale
        number = value.removesuffix(f"/{high}").strip()
        if not number.isdigit() or not low <= int(number) <= high:
            return None
        return f"{int(number)}/{high}"
    if rule.values:
        choice = next((v for v in rule.values if v.casefold() == value.casefold()), None)
        if choice is None:
            return None
        return f"{choice} — {detail}" if detail else choice
    return value


def _parse(raw: str) -> dict:
    payload = json.loads(extract_json_from_response(raw) or "")
    if not isinstance(payload, dict):
        raise ValueError("mapping payload is not an object")
    return payload


def _reports_intent(payload: dict) -> dict:
    reports = payload.get("reports")
    if not isinstance(reports, dict) or not isinstance(reports.get("has_reports"), bool):
        return {}
    wants_upload = reports.get("wants_upload")
    return {
        "has_reports": reports["has_reports"],
        "wants_upload": wants_upload if isinstance(wants_upload, bool) else None,
    }


def _keyed(slots: list[_Slot]) -> dict[str, _Slot]:
    """Short keys the model returns instead of copying long field names."""
    return {f"F{number}": slot for number, slot in enumerate(slots, 1)}


def _build_prompt(
    reply: str,
    last_question: str,
    form: dict,
    slots: list[_Slot],
    visit_context: str,
) -> str:
    unanswered, answered = [], []
    for key, slot in _keyed(slots).items():
        if _is_answered(form, slot):
            answered.append(f"- {key}: {slot.section} — {slot.field} — {_saved_value(form, slot)}")
        else:
            unanswered.append(f"- {key}: {slot.section} — {slot.field} — {slot.question}")
    return _PROMPT.format(
        last_question=last_question.strip() or "(nothing yet)",
        reply=reply.strip(),
        visit_context=visit_context,
        unanswered="\n".join(unanswered) or "(none)",
        answered="\n".join(answered) or "(none)",
        rules=_rules_text(),
    )


def map_reply(
    *,
    reply: str,
    last_question: str,
    form: dict,
    form_sections: list[str],
    visit_context: str,
    llm: Callable[[str], str],
) -> ReplyMapping:
    """Map ``reply`` onto every field it answers, in any order."""

    slots = _slots(form, form_sections)
    try:
        payload = _parse(llm(_build_prompt(reply, last_question, form, slots, visit_context)))
    except Exception as exc:
        print(f"[answer_mapping] Mapping failed: {error_type(exc)}")
        return ReplyMapping(form=form, visit_context=visit_context)

    proposed = payload.get("visit_context")
    next_context = _next_visit_context(
        visit_context, proposed if proposed in VISIT_CONTEXTS else "unknown"
    )
    updated = apply_visit_type(form, visit_context, next_context)

    # The model is asked for the short key; an exact field id is equally unambiguous.
    by_id = {**{slot.field_id: slot for slot in slots}, **_keyed(slots)}
    returned = [item for item in payload.get("answers") or [] if isinstance(item, dict)]
    answers = [
        (by_id[item["field_id"]], item) for item in returned
        if item.get("field_id") in by_id and item.get("status") in _STATUSES
    ]
    if len(answers) < len(returned):
        print(f"[answer_mapping] Discarded {len(returned) - len(answers)} answers with an unknown field or status")
    # A dependent field is settled by its parent, which may arrive in the same reply.
    answers.sort(key=lambda pair: bool(_FORM.rule(pair[0].field).applies_when_answered))

    updated_ids: list[str] = []
    for slot, item in answers:
        status = item["status"]
        if _is_answered(updated, slot) and item.get("correction") is not True:
            continue
        value = _accepted_value(
            status,
            str(item.get("value") or "").strip(),
            str(item.get("detail") or "").strip(),
            slot,
        )
        if value is None:
            continue
        _write(updated, slot, value)
        _settle_dependents(updated, slot.section, slot.field, status == "answered")
        updated_ids.append(slot.field_id)

    print(f"[answer_mapping] Mapped {len(updated_ids)} fields, visit_context={next_context}")
    return ReplyMapping(
        form=updated,
        visit_context=next_context,
        updated_field_ids=list(dict.fromkeys(updated_ids)),
        reports_intent=_reports_intent(payload),
        complaints=list(dict.fromkeys(
            str(item).strip() for item in payload.get("complaints") or []
            if isinstance(item, str) and str(item).strip()
        )),
    )


def choose_focus(reply: str, complaints: list[str], llm: Callable[[str], str]) -> int | None:
    """Return the index of the complaint the patient chose, or None if unclear."""

    options = "\n".join(f"{number}. {label}" for number, label in enumerate(complaints, 1))
    try:
        choice = _parse(llm(_FOCUS_PROMPT.format(options=options, reply=reply.strip()))).get("choice")
    except Exception as exc:
        print(f"[answer_mapping] Focus choice failed: {error_type(exc)}")
        return None
    if isinstance(choice, int) and not isinstance(choice, bool) and 1 <= choice <= len(complaints):
        return choice - 1
    return None
