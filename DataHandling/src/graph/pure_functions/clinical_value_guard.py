"""Final safety and data-quality checks for extracted intake values.

The extractor is probabilistic; persistence is not.  This module rejects
model commentary, advice-seeking questions, and obvious transcription noise
before an extracted value can replace a trusted form value.
"""

from __future__ import annotations

import copy
import re


_MODEL_COMMENTARY = re.compile(
    r"\b(?:likely referring|not specific enough|cannot determine|can't determine|"
    r"could mean|appears to mean|seems to mean|unclear what|ambiguous|"
    r"insufficient information|not enough information|assum(?:e|ed|ing)|"
    r"probably referring|possibly referring)\b",
    re.I,
)

_BODY_LOCATION = re.compile(
    r"\b(?:head|face|jaw|neck|back|spine|shoulder|arm|upper arm|forearm|elbow|"
    r"wrist|hand|finger|thumb|chest|rib|abdomen|stomach|pelvis|hip|groin|"
    r"buttock|glute|thigh|leg|knee|calf|shin|ankle|foot|feet|heel|toe|"
    r"whole body|entire body|all over)\b",
    re.I,
)

_VALID_LOCATION_SENTINELS = re.compile(
    r"^(?:not applicable|no pain reported|not specified|none)(?:\b|\s*[—-])",
    re.I,
)

_DURATION_SIGNAL = re.compile(
    r"\b(?:(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"a|an|couple|few|several)\s*(?:minutes?|hours?|days?|weeks?|months?|years?)|"
    r"since\b|today\b|yesterday\b|recently\b|last\s+(?:night|day|week|month|year)|"
    r"for\s+(?:a\s+)?(?:while|long time))",
    re.I,
)

_FUTURE_OR_TRANSCRIPTION_NOISE = re.compile(
    r"\b(?:tomorrow|next\s+(?:day|week|month|year)|dealt with|will start|"
    r"going to start)\b",
    re.I,
)

_MEDICATION_ACTIVITY_ADVICE = re.compile(
    r"\b(?:painkillers?|medicine|medication|tablets?|ibuprofen|paracetamol|"
    r"acetaminophen|aspirin|naproxen|diclofenac)\b.*\b(?:play|playing|return|"
    r"resume|start|sport|gym|exercise|take|eat|safe)\b|"
    r"\b(?:play|playing|return|resume|start|sport|gym|exercise|take|eat|safe)\b"
    r".*\b(?:painkillers?|medicine|medication|tablets?|ibuprofen|paracetamol|"
    r"acetaminophen|aspirin|naproxen|diclofenac)\b",
    re.I,
)


def _is_rejected_value(section: str, field: str, value: object) -> bool:
    text = " ".join(str(value or "").split())
    if not text:
        return False
    if _MODEL_COMMENTARY.search(text):
        return True

    if field == "Primary Location of Pain":
        if _VALID_LOCATION_SENTINELS.search(text):
            return False
        return _BODY_LOCATION.search(text) is None

    if field == "Duration of the Issue":
        if text.lower().startswith("not applicable"):
            return False
        return bool(_FUTURE_OR_TRANSCRIPTION_NOISE.search(text)) or not bool(
            _DURATION_SIGNAL.search(text)
        )

    if section == "Present Complaint" and _FUTURE_OR_TRANSCRIPTION_NOISE.search(text):
        return True

    if section == "Treatment Goals":
        return bool(_MEDICATION_ACTIVITY_ADVICE.search(text))

    return False


def sanitize_extracted_form(original_form: dict, extracted_form: dict) -> dict:
    """Restore the previous value whenever a newly extracted value is unsafe.

    Restoring rather than blanking protects valid data already collected.  If
    the prior value was empty, normal missing-field logic will ask the patient
    for a clear answer on the next turn.
    """
    result = copy.deepcopy(extracted_form)
    for section, fields in result.items():
        if not isinstance(fields, dict):
            continue
        previous_fields = original_form.get(section, {})
        if not isinstance(previous_fields, dict):
            previous_fields = {}
        for field, value in list(fields.items()):
            previous = previous_fields.get(field, "")
            if value == previous:
                continue
            if _is_rejected_value(section, field, value):
                fields[field] = previous
                print(f"[clinical_value_guard] Rejected unsafe {section}.{field}")
    return result


_PAIN_FACTOR_QUESTION_SIGNALS = {
    "Aggravating Factors": re.compile(
        r"\b(?:what|which).{0,45}(?:makes?|make).{0,20}(?:worse|painful)\b|"
        r"\baggravat(?:e|es|ing|ing factors?)\b|"
        r"\bactivities?.{0,30}(?:worse|increase(?:s)? (?:the )?pain)\b",
        re.I,
    ),
    "Relieving Factors": re.compile(
        r"\b(?:what|which).{0,45}(?:makes?|make).{0,20}(?:better|relief)\b|"
        r"\b(?:reliev(?:e|es|ing)|provides? relief|gives? relief)\b",
        re.I,
    ),
}

_PAIN_FACTOR_ANSWER_SIGNALS = {
    "Aggravating Factors": re.compile(
        r"\b(?:makes? (?:it|the pain|my pain) worse|worsens?|aggravat(?:e|es|ing)|"
        r"increase(?:s|d)? (?:the |my )?pain|pain (?:increase(?:s|d)?|gets worse)|"
        r"(?:gets?|becomes?) (?:even )?worse (?:when|with|while)|"
        r"worse (?:when|with|while)|triggers? (?:the |my )?pain|"
        r"hurts? (?:more )?(?:when|while)|painful (?:when|while))\b",
        re.I,
    ),
    "Relieving Factors": re.compile(
        r"\b(?:makes? (?:it|the pain|my pain) better|helps? (?:it|the pain|my pain)|"
        r"makes? it feel better|"
        r"(?:rest|resting|ice|icing|lying|standing|sleeping)\b.{0,30}\bhelps?|"
        r"reliev(?:e|es|ed|ing)|provides? relief|gives? relief|"
        r"reduces? (?:the |my )?pain|eases? (?:the |my )?pain|"
        r"(?:when|while)\b.{0,55}\bfeel(?:s)? (?:fine|normal|okay|ok|better))\b",
        re.I,
    ),
}

_EXPLICIT_AGGRAVATING = re.compile(
    r"\b(?:makes? (?:it|the pain|my pain) (?:even )?worse|worsens?|"
    r"aggravat(?:e|es|ing)|increase(?:s|d)? (?:the |my )?pain|"
    r"pain (?:increase(?:s|d)?|gets worse)|"
    r"(?:gets?|becomes?) (?:even )?worse (?:when|with|while)|"
    r"worse (?:when|with|while)|triggers? (?:the |my )?pain|"
    r"hurts? (?:more )?(?:when|while)|painful (?:when|while)|"
    r"starts? (?:to )?(?:pain|hurt)|"
    r"starts? paining|pains? while|go(?:es)? numb|causes? (?:pain|numbness))\b",
    re.I,
)
_EXPLICIT_RELIEVING = re.compile(
    r"\b(?:makes? (?:it|the pain|my pain) better|helps? (?:it|the pain|my pain)|"
    r"makes? it feel better|"
    r"(?:rest|resting|ice|icing|lying|standing|sleeping)\b.{0,30}\bhelps?|"
    r"reliev(?:e|es|ed|ing)|provides? relief|gives? (?:any )?relief|"
    r"reduces? (?:the |my )?pain|eases? (?:the |my )?pain|"
    r"nothing .{0,50} (?:helps?|reliev(?:e|es)|gives? (?:any )?relief)|"
    r"(?:when|while)\b.{0,55}\bfeel(?:s)? (?:fine|normal|okay|ok|better))\b",
    re.I,
)
_ALREADY_ANSWERED = re.compile(
    r"\b(?:i (?:already|just) (?:answered|told|said|gave)|"
    r"already (?:answered|told|provided|shared)|i did that)\b",
    re.I,
)


def is_already_answered_response(text: str) -> bool:
    return bool(_ALREADY_ANSWERED.search(" ".join(str(text or "").split())))


def explicit_pain_factor_values(text: str) -> dict[str, str]:
    """Extract only explicitly stated worse/better relationships.

    This is deliberately not a general NLP extractor. It provides a reliable
    fallback for direct answers that the model occasionally leaves blank.
    """

    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return {}
    clauses = [
        clause.strip(" ,.;")
        for clause in re.split(r"(?<=[.!?;])\s+|\s*,\s*(?=[A-Z])", normalized)
        if clause.strip(" ,.;")
    ]
    aggravating = [clause for clause in clauses if _EXPLICIT_AGGRAVATING.search(clause)]
    relieving = [clause for clause in clauses if _EXPLICIT_RELIEVING.search(clause)]
    # A comma often separates a leading relationship clause from its outcome:
    # "When I stand normally, I feel fine." The generic clause splitter cannot
    # retain that relationship, so fall back to the complete sentence only when
    # the same strict evidence regex matches it.
    if not aggravating and _EXPLICIT_AGGRAVATING.search(normalized):
        aggravating = [normalized.strip(" ,.;")]
    if not relieving and _EXPLICIT_RELIEVING.search(normalized):
        relieving = [normalized.strip(" ,.;")]
    result: dict[str, str] = {}
    if aggravating:
        result["Aggravating Factors"] = "; ".join(dict.fromkeys(aggravating))
    if relieving:
        result["Relieving Factors"] = "; ".join(dict.fromkeys(relieving))
    return result


def apply_explicit_pain_factor_answers(
    form: dict,
    *,
    active_section: str,
    user_input: str,
    history: list | None = None,
) -> dict:
    """Fill explicit factor answers into the active complaint only."""

    if active_section != "Pain Assessment" and not active_section.startswith(
        "Additional Complaint "
    ):
        return copy.deepcopy(form)

    source_text = user_input
    if is_already_answered_response(user_input) and history:
        source_text = next(
            (
                str(entry.get("message") or "")
                for entry in reversed(history)
                if entry.get("role") == "user"
                and not is_already_answered_response(entry.get("message", ""))
            ),
            user_input,
        )
    values = explicit_pain_factor_values(source_text)
    if not values:
        return copy.deepcopy(form)

    result = copy.deepcopy(form)
    target = result.get(active_section)
    if not isinstance(target, dict):
        return result
    for field, value in values.items():
        if field in target and not str(target.get(field) or "").strip():
            target[field] = value
    return result


def guard_new_pain_factor_evidence(
    original_form: dict,
    extracted_form: dict,
    *,
    user_input: str | None,
    last_question: str | None,
    current_section: str | None,
) -> dict:
    """Reject newly inferred aggravating/relieving factors without evidence.

    Mechanism-of-injury phrases such as ``playing football`` are not evidence
    that the activity currently makes pain worse.  Likewise, an answer about
    an additional complaint must not populate the primary complaint's pain
    factors.  A factor is accepted only when the current patient turn states
    the relationship explicitly, or when it directly answers the matching
    question for the same complaint scope.
    """

    result = copy.deepcopy(extracted_form)
    answer = " ".join(str(user_input or "").split())
    question = " ".join(str(last_question or "").split())
    active_section = str(current_section or "")
    active_is_additional = active_section.startswith("Additional Complaint ")

    for section, fields in result.items():
        if not isinstance(fields, dict):
            continue
        target_is_additional = str(section).startswith("Additional Complaint ")
        for field in ("Aggravating Factors", "Relieving Factors"):
            if field not in fields:
                continue
            previous = original_form.get(section, {}).get(field, "")
            candidate = fields.get(field, "")
            if candidate == previous or not str(candidate or "").strip():
                continue

            # Compound question + short answer (for example "Walking") is
            # valid only for the complaint section currently being collected.
            same_scope = (
                (active_is_additional and section == active_section)
                or (not active_is_additional and not target_is_additional)
            )
            answers_matching_question = bool(
                same_scope and _PAIN_FACTOR_QUESTION_SIGNALS[field].search(question)
            )
            states_relationship = bool(
                _PAIN_FACTOR_ANSWER_SIGNALS[field].search(answer)
            )

            # During an additional-complaint turn, even explicit factor words
            # belong to that complaint unless the intake explicitly routes a
            # correction through the correction flow.
            explicit_in_scope = states_relationship and same_scope
            if not answers_matching_question and not explicit_in_scope:
                fields[field] = previous
                print(
                    f"[clinical_value_guard] Rejected unsupported "
                    f"{section}.{field}"
                )

    return result
