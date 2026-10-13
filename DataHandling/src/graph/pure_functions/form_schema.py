"""Schema-driven target resolution for patient-requested form corrections."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re


@dataclass(frozen=True)
class FormTarget:
    section: str
    field: str
    component: str | None = None


def _key(value: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


_LIFESTYLE_COMPONENTS = {
    "work": "work",
    "job": "work",
    "occupation": "work",
    "profession": "work",
    "activity": "activity",
    "physical activity": "activity",
    "exercise": "activity",
    "exercise routine": "activity",
    "activity exercise": "activity",
    "smoke": "smoking",
    "smoking": "smoking",
    "tobacco": "smoking",
    "drink": "alcohol",
    "drinking": "alcohol",
    "alcohol": "alcohol",
    "alcohol use": "alcohol",
}


_FIELD_ALIASES = {
    "complaint": ("Present Complaint", "Primary Complaint"),
    "main complaint": ("Present Complaint", "Primary Complaint"),
    "primary complaint": ("Present Complaint", "Primary Complaint"),
    "duration": ("Present Complaint", "Duration of the Issue"),
    "how long": ("Present Complaint", "Duration of the Issue"),
    "onset": ("Present Complaint", "Onset (Gradual or Sudden)"),
    "start": ("Present Complaint", "Onset (Gradual or Sudden)"),
    "cause": ("Present Complaint", "Mechanism of Injury or Cause"),
    "mechanism": ("Present Complaint", "Mechanism of Injury or Cause"),
    "previous consultation": (
        "Previous Consultations",
        "Previous Diagnosis or Advice and Prescribed Treatment Taken",
    ),
    "previous treatment": (
        "Previous Consultations",
        "Previous Diagnosis or Advice and Prescribed Treatment Taken",
    ),
    "diagnosis": (
        "Previous Consultations",
        "Previous Diagnosis or Advice and Prescribed Treatment Taken",
    ),
    "current status": (
        "Previous Consultations",
        "Current Status of Issue (Improved, Same, Worse)",
    ),
    "improvement": (
        "Previous Consultations",
        "Current Status of Issue (Improved, Same, Worse)",
    ),
    "pain location": ("Pain Assessment", "Primary Location of Pain"),
    "location": ("Pain Assessment", "Primary Location of Pain"),
    "body part": ("Pain Assessment", "Primary Location of Pain"),
    "severity": ("Pain Assessment", "Severity (1-10)"),
    "pain score": ("Pain Assessment", "Severity (1-10)"),
    "pain rating": ("Pain Assessment", "Severity (1-10)"),
    "aggravating factors": ("Pain Assessment", "Aggravating Factors"),
    "makes it worse": ("Pain Assessment", "Aggravating Factors"),
    "relieving factors": ("Pain Assessment", "Relieving Factors"),
    "relief": ("Pain Assessment", "Relieving Factors"),
    "makes it better": ("Pain Assessment", "Relieving Factors"),
    "health history": (
        "History & Diagnostics",
        "Systemic Illness and Surgical History",
    ),
    "health conditions": (
        "History & Diagnostics",
        "Systemic Illness and Surgical History",
    ),
    "surgery": (
        "History & Diagnostics",
        "Systemic Illness and Surgical History",
    ),
    "lifestyle": ("History & Diagnostics", "Current Lifestyle"),
    "reports": ("History & Diagnostics", "Reports"),
    "documents": ("History & Diagnostics", "Reports"),
    "scans": ("History & Diagnostics", "Reports"),
    "short term goal": ("Treatment Goals", "Short-Term Goals (within 3 months)"),
    "three month goal": ("Treatment Goals", "Short-Term Goals (within 3 months)"),
    "long term goal": ("Treatment Goals", "Long-Term Goals (after 3 months)"),
    "expectation": ("Treatment Goals", "Specific Expectations from Treatment"),
    "treatment expectation": (
        "Treatment Goals",
        "Specific Expectations from Treatment",
    ),
    "referral": ("Referral", "Source"),
    "source": ("Referral", "Source"),
    "heard about us": ("Referral", "Source"),
}


def resolve_form_target(
    form: dict,
    section_hint: object,
    field_hint: object,
) -> FormTarget | None:
    """Resolve model/user terminology to one unambiguous form target."""
    section_key = _key(section_hint)
    field_key = _key(field_hint)

    component = _LIFESTYLE_COMPONENTS.get(field_key)
    if component and section_key in {
        "", "current lifestyle", "lifestyle", "history diagnostics"
    }:
        diagnostics = form.get("History & Diagnostics")
        if isinstance(diagnostics, dict) and "Current Lifestyle" in diagnostics:
            return FormTarget(
                "History & Diagnostics", "Current Lifestyle", component
            )

    candidates: list[FormTarget] = []
    for section, fields in form.items():
        if not isinstance(fields, dict):
            continue
        for field in fields:
            candidates.append(FormTarget(section, field))

    # Exact schema names always win.
    exact = [
        target for target in candidates
        if _key(target.field) == field_key
        and (not section_key or _key(target.section) == section_key)
    ]
    if len(exact) == 1:
        return exact[0]

    # A unique exact field is safe even if the model supplied a loose section.
    exact_field = [target for target in candidates if _key(target.field) == field_key]
    if len(exact_field) == 1:
        return exact_field[0]
    if len(exact_field) > 1 and not section_key:
        return None

    alias = _FIELD_ALIASES.get(field_key)
    if alias:
        hinted_sections = [
            section for section, fields in form.items()
            if isinstance(fields, dict) and _key(section) == section_key
        ]
        if len(hinted_sections) == 1:
            hinted_section = hinted_sections[0]
            alias_field = alias[1]
            if alias_field in form[hinted_section]:
                return FormTarget(hinted_section, alias_field)
        target = FormTarget(*alias)
        if target.section in form and target.field in form[target.section]:
            return target

    # Conservative fuzzy resolution handles small model spelling differences,
    # but only when there is one strong winner. Never guess between close fields.
    scored = [
        (
            SequenceMatcher(None, field_key, _key(target.field)).ratio(),
            target,
        )
        for target in candidates
        if field_key
    ]
    scored.sort(key=lambda item: item[0])
    if not scored:
        return None
    best_score, best = scored[-1]
    second_score = scored[-2][0] if len(scored) > 1 else 0.0
    if best_score >= 0.88 and best_score - second_score >= 0.08:
        return best
    return None
