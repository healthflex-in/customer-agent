"""Deterministic restoration of a persisted FRM-01 attempt."""

from __future__ import annotations

from dataclasses import dataclass

from src.forms.loader import load_form
from src.graph.pure_functions.form_validation import validate_section
from src.graph.pure_functions.question_plan import contextual_resume_question
from src.graph.pure_functions.summary import _fallback_summary
from src.prompts import INITIAL_INTAKE_PROMPT


@dataclass(frozen=True)
class ResumePlan:
    phase: str
    current_section: str
    question_round: int
    missing: list[tuple[str, str]]
    message: str

    @property
    def question_field_ids(self) -> list[str]:
        return [f"{section}.{field}" for section, field in self.missing[:4]]


def build_resume_plan(form: dict, persisted_section: str = "Present Complaint") -> ResumePlan:
    """Restore interview/summary state from the persisted structured form.

    MongoDB is the assessment source of truth. Conversation phase was not
    historically persisted, so an attempt with no required gaps is restored to
    summary—not completed—until the patient explicitly confirms it.
    """

    form_sections = list(form)
    missing = [
        (section, field)
        for section in form_sections
        for field in validate_section(form, section)
    ]
    has_content = any(
        value is not None and str(value).strip()
        for fields in form.values()
        if isinstance(fields, dict)
        for value in fields.values()
    )

    if not has_content:
        return ResumePlan(
            phase="interviewing",
            current_section="Present Complaint",
            question_round=0,
            missing=missing,
            message=INITIAL_INTAKE_PROMPT,
        )

    if not missing:
        return ResumePlan(
            phase="summary",
            current_section=persisted_section or form_sections[-1],
            question_round=3,
            missing=[],
            message=_fallback_summary(form),
        )

    first_section = missing[0][0]
    canonical_round = {
        "Present Complaint": 0,
        "Previous Consultations": 0,
        "Pain Assessment": 1,
        "History & Diagnostics": 1,
        "Treatment Goals": 2,
        "Referral": 2,
    }
    question_round = canonical_round.get(first_section, 2)
    definition = load_form("FRM-01")
    return ResumePlan(
        phase="interviewing",
        current_section=first_section,
        question_round=question_round,
        missing=missing,
        message=contextual_resume_question(form, missing, definition.field_labels),
    )
