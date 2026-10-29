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
from typing import Callable, Sequence

from app.observability.privacy import error_type
from src.forms.loader import load_form
from src.graph.pure_functions.additional_complaint import same_complaint
from src.graph.pure_functions.form_validation import extract_json_from_response
from src.graph.pure_functions.intent_detection import bare_yes_no
from src.graph.pure_functions.phrasing import normalise_intent, normalise_tone
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
# Saved when the patient skips an optional question; the summary shows it as written.
DECLINED = "Skipped by patient"
VISIT_CONTEXTS = ("specific_complaint", "general_assessment", "unknown")

_FORM = load_form("FRM-01")
_LIFESTYLE_FIELD = "Current Lifestyle"
_STATUSES = {"answered", "denied", "unsure", "declined"}
_COMPONENT_OF = {name: component for component, name in LIFESTYLE_COMPONENT_FIELDS.items()}
# These are not answered by naming today's problem. A value made only of those
# words is left empty so Sage still asks the question.
_NOT_FROM_COMPLAINT = {
    "Previous Diagnosis or Advice and Prescribed Treatment Taken",
}
# Sections a general visit skips are the ones that describe a single complaint.
COMPLAINT_SECTIONS = _FORM.visit_types["general_assessment"].skip_sections

_PROMPT = """You map a patient's reply in a physiotherapy intake interview onto intake form fields.

Sage (the assistant) last said:
{last_question}{complaint_focus}

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
- Naming today's problem fills Primary Complaint only. Do not copy that problem, or a shortened form of its name, into any other field.
- Previous Diagnosis or Advice and Prescribed Treatment Taken is filled only when the patient says they were seen, diagnosed, or treated before. The value is that visit, diagnosis, or advice. The name of today's problem is not a previous diagnosis, and it must not be saved ther
- Primary Location of Pain is the body area where the patient feels the problem. Fill it from anything the patient has said in this conversation, including inside the complaint: "traps pain" gives the location Traps. A more specific area replaces a general one: "lower back" replaces "back". Never give an area the patient did not mention.
- Aggravating Factors and Relieving Factors can honestly have nothing to report. That is an answer about what happens, not a denial. "Nothing helps" or "nothing gives relief" is answered: value says it in the patient's terms, such as "Nothing gives relief". An injury that happened once and did not come back is answered too: value such as "Happened only once, not repeated". Do not copy the activity that caused a one-off injury into Aggravating Factors; that activity is the Mechanism.
- "answered": the patient gave the information. value is a short, clear restatement of what the patient said. Never add facts, guesses, or commentary.
- For a field with fixed values, value is exactly one of those values, chosen by what the field means. Onset is exactly Gradual or Sudden, and its detail is always empty. For any other fixed field, detail is empty when the patient only named that value. Put a detail only when they gave an explanation the value does not already say. Other fields leave detail empty.
- "denied": the patient said there is none. value is a short negative statement in the patient's terms.
- "unsure": the patient does not know or does not remember. value is a short statement saying so.
- A short yes or no answers the question it fits. "No", "none" or "nothing" to a yes-or-no question is an answer, with status "answered" and value "No" or "Yes", or a short statement such as "No other health conditions" or "No reports". It is never "declined" and never a skip. "No I don't have reports" is answered "No reports". Only "skip", "I don't want to answer" or "I'd rather not say" is "declined". If a bare yes or no fits none of the questions, leave it out.
- "declined": the patient refuses to answer a question Sage just asked ("skip", "I don't want to answer", "I'd rather not say"). This is not "unsure" and not "denied". Name only the questions they refuse. If they refuse the whole batch, decline each question Sage just asked. If they only say skip and Sage asked more than one question, without saying which, set decline_scope to "unspecified" and do not decline individual fields. Otherwise decline_scope is "specified".
- A newly mentioned separate problem is listed under complaints. Do not put its facts into the fields of a different problem.
- When the patient has more than one problem, each field says which problem it is about. Put the facts in the problem the patient is describing. When it is unclear, use the problem Sage's questions were about.
- A body area the patient gives as where they feel the problem (for example "my lower back") fills Primary Location of Pain. It is not a separate problem: do not list it under complaints, and do not list the main problem again in other words.
- A short reply such as "no", "none" or "nothing" answers each field Sage just asked about that it sensibly answers.
- "correction": true only for an already answered field, and only when the patient clearly changes that earlier answer; otherwise false.
- A question the patient asks Sage is not an answer. Leave out fields the reply does not address.
- reports.has_reports: true or false only when the reply says whether the patient has diagnostic reports, otherwise null. reports.wants_upload: false when they will not upload here, true when they want to upload now, otherwise null.
- complaints: every distinct health problem the patient reports in this reply, each as a short clinical label in the patient's terms. Symptoms that belong to one condition, such as pain spreading from one area into another, are one problem. Empty when the reply reports none.
- visit_context: "specific_complaint" when the patient has a pain, injury or condition, "general_assessment" for a check-up or wellness visit without a complaint, otherwise "unknown".
- intent: what the patient is doing in this reply. "answer" when they answer or add information, "skip" when they want to skip a question, "end" when they want to stop or leave the interview, "form_question" when they ask what a question means or which problem it is about, "medical_advice" when they ask for a diagnosis, treatment or medication, "off_topic" for anything unrelated to the intake. When unsure, "answer".
- tone: how the patient seems in this reply. "frustrated" when they are annoyed or want to stop, "confused" when they do not understand the questions or why they are asked, "distressed" when they describe real distress, "positive" when they are relaxed or pleased, otherwise "neutral".

Return only JSON in this shape:
{{"visit_context": "unknown", "answers": [{{"field_id": "F1", "status": "answered", "value": "...", "detail": "", "correction": false}}], "reports": {{"has_reports": null, "wants_upload": null}}, "complaints": [], "decline_scope": "specified", "tone": "neutral", "intent": "answer"}}"""

_OPEN_MESSAGE_PROMPT = """A patient in a physiotherapy intake interview was asked:
{question}

The patient replied:
{reply}

{problems}What is the reply?
- "answer": the patient is answering the question or giving information about their problem. This includes hedged or unsure answers such as "may be a few days", "could be two weeks", "I can't remember" or "I don't know", and answers that start with a word like "when", "where", "what" or "how".
- "form_question": the patient is asking what the question means, which problem it is about, or how to answer it.
- "other": a request for medical advice, a question about the clinic, or an unrelated topic.

Return only JSON: {{"kind": "answer"}}, {{"kind": "form_question"}} or {{"kind": "other"}}"""

_FOCUS_PROMPT = """A patient in a physiotherapy intake interview reported these separate problems:
{options}

Sage asked which one they would like to focus on today. The patient replied:
{reply}

Return only JSON: {{"choice": <number of the chosen problem, or null when the reply does not clearly choose one>}}"""

_SKIP_PROMPT = """Sage asked the patient which of these questions they want to skip:
{options}

The patient replied:
{reply}

Return only JSON: {{"choice": "all"}} when they skip every question, {{"choice": [1, 2]}} for the questions they name, or {{"choice": null}} when the reply does not say."""


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
    declined_field_ids: list[str] = field(default_factory=list)
    decline_unspecified: bool = False
    tone: str = "neutral"
    intent: str = "answer"


_NOT_A_COMPLAINT = frozenset({"", NOT_PROVIDED, NOT_APPLICABLE, "Skipped by patient"})


def complaint_labels(form: dict) -> dict[str, str]:
    """Which problem each complaint section is about, once there is more than one.

    Empty when the patient has a single problem, so nothing changes for the common
    case. With several, every section about the main problem and every added
    complaint maps to the name of the problem it describes.
    """

    main = str((form.get("Present Complaint") or {}).get("Primary Complaint") or "").strip()
    added = {
        section: str(fields.get("Primary Complaint") or "").strip()
        for section, fields in form.items()
        if section.startswith("Additional Complaint ") and isinstance(fields, dict)
    }
    added = {section: label for section, label in added.items() if label}
    if main in _NOT_A_COMPLAINT or not added:
        return {}
    return {**{section: main for section in COMPLAINT_SECTIONS}, **added}


def _slots(form: dict, form_sections: list[str]) -> list[_Slot]:
    slots: list[_Slot] = []
    labels = complaint_labels(form)
    for section in form_sections:
        fields = form.get(section)
        if not isinstance(fields, dict):
            continue
        names = list(dict.fromkeys([*_FORM.sections.get(section, []), *fields]))
        complaint = str(fields.get("Primary Complaint") or "").strip()
        if section.startswith("Additional Complaint ") and complaint:
            about = f" (about the additional complaint: {complaint})"
        elif section in labels:
            about = f" (about the main complaint: {labels[section]})"
        else:
            about = ""
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
            lines.append(
                f"- {name}: a whole number from {rule.scale[0]} to {rule.scale[1]}. "
                "When the patient gives a range such as 8-9, use the highest number in it"
            )
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


def record_not_provided(form: dict, field_ids: list[str], marker: str = NOT_PROVIDED) -> dict:
    """Return a copy of ``form`` with each field id saved as ``marker``."""

    updated = copy.deepcopy(form)
    for field_id in field_ids:
        section, name = field_id.split(".", 1)
        _write(updated, _Slot(field_id, section, name, ""), marker)
        _settle_dependents(updated, section, name, answered=False)
    return updated


def field_still_open(form: dict, field_id: str) -> bool:
    """True when ``field_id`` has no saved answer yet."""

    section, name = field_id.split(".", 1)
    return not _is_answered(form, _Slot(field_id, section, name, ""))


def is_required_field(field_id: str) -> bool:
    """True when the ``Section.Field`` id names a mandatory field."""

    section, field = field_id.split(".", 1)
    return _FORM.is_mandatory(section, field)


def apply_declines(form: dict, field_ids: list[str]) -> tuple[dict, list[str]]:
    """Save each refused optional field that is still open as Declined.

    A mandatory field is never declined: it stays open and is asked again,
    within the ask cap. Returns the form and the ids closed on this turn.
    """

    closed: list[str] = []
    for field_id in dict.fromkeys(field_ids):
        if is_required_field(field_id) or not field_still_open(form, field_id):
            continue
        form = record_not_provided(form, [field_id], DECLINED)
        closed.append(field_id)
    return form, closed


@dataclass(frozen=True)
class SkipDecision:
    """What a refusal means for the questions the patient was just asked."""

    decline_ids: list[str]        # optional fields to close as Declined now
    ask_which: list[str]          # optional questions to offer; empty when no prompt is needed
    mandatory_refused: bool       # the patient refused a mandatory question
    keep_required_note: bool      # the prompt should say mandatory questions stay


def decide_skip(
    *,
    form: dict,
    asked_ids: list[str],
    declined_ids: list[str],
    unspecified: bool,
    ask_counts: dict,
    max_asks: int,
    skip_all: bool = False,
) -> SkipDecision:
    """Turn a refusal into declines, a "which one?" prompt, or a mandatory notice.

    ``declined_ids`` are the fields the patient named; ``unspecified`` means they
    only said skip; ``skip_all`` means they said to skip everything, which declines
    every open optional question at once. Mandatory questions are never declined.
    An optional question already shown ``max_asks`` times is closed elsewhere.
    """

    open_asked = [field_id for field_id in asked_ids if field_still_open(form, field_id)]
    mandatory_asked = [field_id for field_id in open_asked if is_required_field(field_id)]
    skippable = [
        field_id for field_id in open_asked
        if not is_required_field(field_id) and ask_counts.get(field_id, 0) < max_asks
    ]
    named = [field_id for field_id in declined_ids if field_still_open(form, field_id)]
    decline = [field_id for field_id in named if not is_required_field(field_id)]
    mandatory_refused = len(decline) < len(named)

    if skip_all:
        optional_open = [field_id for field_id in open_asked if not is_required_field(field_id)]
        return SkipDecision(
            list(dict.fromkeys([*decline, *optional_open])),
            [], bool(mandatory_asked), False,
        )
    if unspecified:
        if not skippable:
            mandatory_refused = mandatory_refused or bool(mandatory_asked)
        elif len(skippable) + len(mandatory_asked) == 1:
            # The only question still open is optional: a bare skip is clearly about it.
            decline = list(dict.fromkeys([*decline, *skippable]))
        else:
            # Several questions are open, so "skip" alone must not guess which one.
            return SkipDecision(decline, skippable, mandatory_refused, bool(mandatory_asked))
    return SkipDecision(decline, [], mandatory_refused, False)


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


def _words(text: str) -> list[str]:
    words = [
        "".join(character for character in word.casefold() if character.isalnum())
        for word in text.split()
    ]
    return [word for word in words if word]


def _same_stem(left: str, right: str) -> bool:
    """True when two words are the same word or a short form of it.

    "fascia" and "fasciopathy" share this. A one-letter word does not match a
    longer unrelated word.
    """

    if left == right:
        return True
    shared = 0
    for left_character, right_character in zip(left, right):
        if left_character != right_character:
            break
        shared += 1
    if shared < 5:
        return False
    return len(left) - shared <= 2 or len(right) - shared <= 2


def _complaint_text(form: dict, section: str) -> str:
    source = section if section.startswith("Additional Complaint ") else "Present Complaint"
    fields = form.get(source) or {}
    return str(fields.get("Primary Complaint") or "")


_FILLER_WORDS = frozenset({"the", "a", "an", "my", "of", "in", "on", "and", "area", "part", "side"})


def _content_words(text: str) -> list[str]:
    return [word for word in _words(text) if word not in _FILLER_WORDS]


def _patient_mentions(value: str, patient_text: str) -> bool:
    """True when at least one content word of ``value`` appears in what the patient said.

    The location must come from the patient's own words somewhere in the
    conversation, not from the model's guess.
    """

    spoken = _words(patient_text)
    return any(
        any(_same_stem(word, heard) for heard in spoken)
        for word in _content_words(value)
    )


def _is_more_specific(current: str, value: str) -> bool:
    """True when ``value`` names the same area as ``current`` but in more detail."""

    old, new = set(_content_words(current)), set(_content_words(value))
    return bool(old) and old < new


def _is_another_complaints_area(value: str, section: str, labels: dict[str, str]) -> bool:
    """True when ``value`` is the area of a different complaint, not this section's own.

    With a main complaint and a knee complaint, "left knee" is where the knee hurts.
    It must not be saved as the location of the main complaint's pain.
    """

    own = labels.get(section, "")
    others = [label for name, label in labels.items() if label != own]
    return bool(own) and not same_complaint(value, own) and any(
        same_complaint(value, other) for other in others
    )


def _only_restates_complaint(complaint: str, value: str) -> bool:
    """True when every word in ``value`` is already in the complaint name."""

    complaint_words = _words(complaint)
    value_words = _words(value)
    if not complaint_words or not value_words:
        return False
    return all(
        any(_same_stem(word, complaint_word) for complaint_word in complaint_words)
        for word in value_words
    )


def _explanation(choice: str, detail: str) -> str:
    """Return detail only when it adds an explanation beyond the chosen value.

    "suddenly" adds nothing to Sudden. A sentence that also says what happened does.
    """

    text = " ".join(detail.split())
    choice_word = "".join(character for character in choice.casefold() if character.isalnum())
    words = [
        "".join(character for character in word.casefold() if character.isalnum())
        for word in text.split()
    ]
    words = [word for word in words if word]
    if not text or not choice_word or not words:
        return ""
    repeats_choice = all(
        word == choice_word or word.startswith(choice_word) or choice_word.startswith(word)
        for word in words
    )
    return "" if repeats_choice else text


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
        if slot.field.startswith("Onset"):
            return choice
        extra = _explanation(choice, detail)
        return f"{choice} — {extra}" if extra else choice
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
    complaint_focus: str = "",
) -> str:
    unanswered, answered = [], []
    for key, slot in _keyed(slots).items():
        if _is_answered(form, slot):
            answered.append(f"- {key}: {slot.section} — {slot.field} — {_saved_value(form, slot)}")
        else:
            unanswered.append(f"- {key}: {slot.section} — {slot.field} — {slot.question}")
    return _PROMPT.format(
        last_question=last_question.strip() or "(nothing yet)",
        complaint_focus=complaint_focus,
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
    asked_ids: Sequence[str] = (),
    patient_text: str = "",
) -> ReplyMapping:
    """Map ``reply`` onto every field it answers, in any order.

    ``patient_text`` is everything the patient has said so far. A location is
    only saved when the patient mentioned it there, and a more specific location
    replaces a general one. ``asked_ids`` are the questions the patient was just
    asked; a location given in answer to them is always taken.
    """

    slots = _slots(form, form_sections)
    labels = complaint_labels(form)
    try:
        asked_section = next((field_id.split(".", 1)[0] for field_id in asked_ids), "")
        focus = (
            f"\n(Sage's questions were about: {labels[asked_section]}.)"
            if labels and asked_section in labels
            else ""
        )
        payload = _parse(llm(_build_prompt(reply, last_question, form, slots, visit_context, focus)))
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
    answers.sort(key=lambda pair: (
        bool(_FORM.rule(pair[0].field).applies_when_answered),
        pair[0].field != "Primary Complaint",
    ))

    updated_ids: list[str] = []
    declined_ids: list[str] = []
    # A bare yes or no is an answer to the question it fits, never a refusal.
    yes_no = bare_yes_no(reply)
    if yes_no:
        answers = [(slot, {**item, "status": "answered", "value": yes_no})
                   if item["status"] == "declined" else (slot, item)
                   for slot, item in answers]
    for slot, item in answers:
        status = item["status"]
        if status == "declined":
            declined_ids.append(slot.field_id)
            continue
        replaces_location = (
            slot.field == "Primary Location of Pain"
            and _is_more_specific(_saved_value(updated, slot), str(item.get("value") or ""))
        )
        if _is_answered(updated, slot) and item.get("correction") is not True and not replaces_location:
            continue
        value = _accepted_value(
            status,
            str(item.get("value") or "").strip(),
            str(item.get("detail") or "").strip(),
            slot,
        )
        if value is None:
            continue
        if slot.field == "Primary Location of Pain" and not (
            slot.field_id in asked_ids or _patient_mentions(value, patient_text or reply)
        ):
            continue
        if slot.field == "Primary Location of Pain" and _is_another_complaints_area(
            value, slot.section, labels
        ):
            continue
        if (
            slot.field in _NOT_FROM_COMPLAINT
            and slot.field_id not in asked_ids
            and _only_restates_complaint(_complaint_text(updated, slot.section), value)
        ):
            continue
        _write(updated, slot, value)
        _settle_dependents(updated, slot.section, slot.field, status == "answered")
        updated_ids.append(slot.field_id)

    if yes_no and not updated_ids:
        # Nothing was mapped. A bare yes or no answers the one question that was
        # asked; with several asked, the caller asks which one it is for.
        open_asked = [field_id for field_id in asked_ids if field_still_open(updated, field_id)]
        if len(open_asked) == 1:
            slot = next((s for s in slots if s.field_id == open_asked[0]), None)
            if slot is not None:
                _write(updated, slot, yes_no)
                _settle_dependents(updated, slot.section, slot.field, True)
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
        declined_field_ids=list(dict.fromkeys(declined_ids)),
        decline_unspecified=payload.get("decline_scope") == "unspecified" and not yes_no,
        tone=normalise_tone(payload.get("tone")),
        intent=normalise_intent(payload.get("intent")),
    )


def read_open_message(
    reply: str, question: str, problems: list[str], llm: Callable[[str], str]
) -> str:
    """Say what an open message is: "answer", "form_question" or "other".

    Used only for a message the keyword check took for an unrelated question. It
    fails closed: on any doubt or failure the answer is "other", which keeps today's
    boundary reply, so a model problem can never let advice through.
    """

    listed = f"The patient has these problems: {'; '.join(problems)}.\n\n" if problems else ""
    try:
        payload = _parse(llm(_OPEN_MESSAGE_PROMPT.format(
            question=question.strip(), reply=reply.strip(), problems=listed
        )))
    except Exception as exc:
        print(f"[answer_mapping] Open-message check failed: {error_type(exc)}")
        return "other"
    kind = payload.get("kind")
    reading = kind if kind in ("answer", "form_question") else "other"
    print(f"[answer_mapping] Open message read as {reading}")
    return reading


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


def choose_skipped(reply: str, questions: list[str], llm: Callable[[str], str]) -> list[int] | None:
    """Return indexes of the questions the patient chose to skip, or None if unclear.

    Every index means they skipped the whole list.
    """

    options = "\n".join(f"{number}. {label}" for number, label in enumerate(questions, 1))
    try:
        choice = _parse(llm(_SKIP_PROMPT.format(options=options, reply=reply.strip()))).get("choice")
    except Exception as exc:
        print(f"[answer_mapping] Skip choice failed: {error_type(exc)}")
        return None
    if choice == "all":
        return list(range(len(questions)))
    if isinstance(choice, list):
        indexes = [
            item - 1 for item in choice
            if isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= len(questions)
        ]
        return indexes or None
    return None
