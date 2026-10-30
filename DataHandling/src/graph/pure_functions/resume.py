"""Deterministic restoration of a persisted FRM-01 attempt."""

from __future__ import annotations

from dataclasses import dataclass

from src.forms.loader import load_form
from src.graph.pure_functions.form_validation import first_section_batch, missing_in_ask_order
from src.graph.pure_functions.question_plan import (
    QUESTIONS_PER_TURN,
    contextual_resume_question,
    question_for_missing_fields,
)
from src.graph.pure_functions.summary import _fallback_summary
from src.prompts import INITIAL_INTAKE_PROMPT, OPENING_QUESTION


@dataclass(frozen=True)
class ResumePlan:
    phase: str
    current_section: str
    question_round: int
    missing: list[tuple[str, str]]
    message: str

    @property
    def question_field_ids(self) -> list[str]:
        batch = first_section_batch(self.missing, QUESTIONS_PER_TURN)
        return [f"{section}.{field}" for section, field in batch]

    def agent_entry(self, message: str) -> dict:
        """Graph-history entry for the message shown on resume.

        It names the questions the message asks, so a skip sent right after a
        refresh refers to them, and stores the questions on their own, so a
        repeat never quotes the "Welcome back" lines said in front of them. Only a
        message that asks specific questions carries these; the opening prompt and
        the summary do not.
        """

        entry = {"role": "agent", "message": message}
        if message == INITIAL_INTAKE_PROMPT:
            entry["question_text"] = OPENING_QUESTION
        asks_questions = (
            self.phase == "interviewing"
            and message == self.message
            and message != INITIAL_INTAKE_PROMPT
        )
        if asks_questions:
            entry["question_field_ids"] = self.question_field_ids
            entry["question_text"] = question_for_missing_fields(
                first_section_batch(self.missing, QUESTIONS_PER_TURN),
                load_form("FRM-01").field_labels,
            )
        return entry


def build_resume_plan(form: dict, persisted_section: str = "Present Complaint") -> ResumePlan:
    """Restore interview/summary state from the persisted structured form.

    MongoDB is the assessment source of truth. Conversation phase was not
    historically persisted, so an attempt with no required gaps is restored to
    summary—not completed—until the patient explicitly confirms it.
    """

    form_sections = list(form)
    missing = missing_in_ask_order(form, form_sections)
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
    }
    question_round = canonical_round.get(first_section, 2)
    definition = load_form("FRM-01")
    return ResumePlan(
        phase="interviewing",
        current_section=first_section,
        question_round=question_round,
        missing=missing,
        message=contextual_resume_question(
            form, first_section_batch(missing, QUESTIONS_PER_TURN), definition.field_labels
        ),
    )
