"""When the interview may finish early: mandatory answers present, enough of the form done."""

from __future__ import annotations

from dataclasses import dataclass

from app.forms.progress import calculate_form_progress
from src.forms.loader import load_form
from src.graph.pure_functions.form_validation import missing_in_ask_order


@dataclass(frozen=True)
class CompletionStatus:
    mandatory_missing: list[tuple[str, str]]  # mandatory fields without a real answer
    percent: float                            # share of the form closed, as the progress bar shows
    can_finish: bool


def completion_status(
    form: dict,
    form_sections: list,
    visit_context: str,
    min_percent: float,
) -> CompletionStatus:
    """Decide whether a patient who asks to end now may do so.

    No mandatory question may still be open: one the bot can still ask holds the
    patient back, while one already closed after three asks does not. The form
    must also be at least ``min_percent`` done by the progress bar's measure.
    """

    definition = load_form("FRM-01")
    mandatory_missing = [
        (section, field)
        for section, field in missing_in_ask_order(form, form_sections, visit_context)
        if definition.is_mandatory(section, field)
    ]
    # Additional complaints are optional, so they never hold back finishing.
    main_intake = {
        section: fields for section, fields in form.items()
        if not section.startswith("Additional Complaint ")
    }
    percent = calculate_form_progress(main_intake)
    return CompletionStatus(
        mandatory_missing=mandatory_missing,
        percent=percent,
        can_finish=not mandatory_missing and percent >= min_percent,
    )
