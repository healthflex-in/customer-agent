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
