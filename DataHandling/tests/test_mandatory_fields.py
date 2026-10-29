"""Mandatory intake fields: which they are, that they come first, and that they cannot be skipped."""

import unittest

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.answer_mapping import DECLINED, apply_declines, is_required_field


MANDATORY = frozenset({
    "Primary Complaint",
    "Duration of the Issue",
    "Onset (Gradual or Sudden)",
    "Mechanism of Injury or Cause",
    "Primary Location of Pain",
    "Severity (1-10)",
    "Aggravating Factors",
})

OPTIONAL_SAMPLE = (
    "Relieving Factors",
    "Previous Diagnosis or Advice and Prescribed Treatment Taken",
    "Systemic Illness and Surgical History",
    "Current Lifestyle",
    "Reports",
    "Short-Term Goals (within 3 months)",
    "Long-Term Goals (after 3 months)",
    "Specific Expectations from Treatment",
)


def _state(form: dict) -> dict:
    return {
        "form": form,
        "form_sections": list(form),
        "visit_context": "specific_complaint",
        "question_ask_counts": {},
        "tagged_form_template": None,
        "decline_notice": False,
        "noted_complaints": [],
    }


class RequiredFieldDefinitionTests(unittest.TestCase):
    def test_specific_complaint_requires_exactly_the_agreed_fields(self):
        self.assertEqual(load_form("FRM-01").required_fields("specific_complaint"), MANDATORY)

    def test_general_assessment_requires_none_of_the_complaint_fields(self):
        self.assertEqual(load_form("FRM-01").required_fields("general_assessment"), frozenset())

    def test_history_lifestyle_and_goals_stay_optional(self):
        form = load_form("FRM-01")
        for field in OPTIONAL_SAMPLE:
            with self.subTest(field=field):
                self.assertFalse(form.is_required(field))


class DeclineGuardTests(unittest.TestCase):
    def test_mandatory_field_is_never_saved_as_declined(self):
        form = load_form("FRM-01").empty_form()
        ids = ["Pain Assessment.Severity (1-10)", "Present Complaint.Primary Complaint"]

        updated, closed = apply_declines(form, ids)

        self.assertEqual(closed, [])
        self.assertEqual(updated["Pain Assessment"]["Severity (1-10)"], "")
        self.assertEqual(updated["Present Complaint"]["Primary Complaint"], "")

    def test_optional_field_is_declined_in_the_same_call(self):
        form = load_form("FRM-01").empty_form()
        ids = ["Pain Assessment.Severity (1-10)", "Pain Assessment.Relieving Factors"]

        updated, closed = apply_declines(form, ids)

        self.assertEqual(closed, ["Pain Assessment.Relieving Factors"])
        self.assertEqual(updated["Pain Assessment"]["Relieving Factors"], DECLINED)
        self.assertEqual(updated["Pain Assessment"]["Severity (1-10)"], "")

    def test_additional_complaint_questions_are_never_mandatory(self):
        form = load_form("FRM-01")

        self.assertTrue(is_required_field("Pain Assessment.Severity (1-10)"))
        self.assertFalse(is_required_field("Additional Complaint 1.Severity (1-10)"))
        self.assertFalse(is_required_field("Additional Complaint 1.Primary Complaint"))
        self.assertTrue(form.is_mandatory("Present Complaint", "Primary Complaint"))
        self.assertFalse(form.is_mandatory("Additional Complaint 2", "Primary Complaint"))

    def test_an_additional_complaint_question_can_be_declined(self):
        form = load_form("FRM-01").empty_form()
        form["Additional Complaint 1"] = {"Primary Complaint": "Knee pain", "Severity (1-10)": ""}

        updated, closed = apply_declines(form, ["Additional Complaint 1.Severity (1-10)"])

        self.assertEqual(closed, ["Additional Complaint 1.Severity (1-10)"])
        self.assertEqual(updated["Additional Complaint 1"]["Severity (1-10)"], DECLINED)


class AskOrderTests(unittest.TestCase):
    def test_mandatory_pain_fields_are_asked_before_optional_earlier_sections(self):
        form = load_form("FRM-01").empty_form()
        for name in form["Present Complaint"]:
            form["Present Complaint"][name] = "Provided"

        step = next_intake_step(_state(form), [])

        # Previous Consultations comes before Pain Assessment in the form,
        # but the mandatory pain questions are asked first.
        self.assertEqual(
            step["question_field_ids"][:3],
            [
                "Pain Assessment.Primary Location of Pain",
                "Pain Assessment.Severity (1-10)",
                "Pain Assessment.Aggravating Factors",
            ],
        )

    def test_all_mandatory_fields_precede_every_optional_field(self):
        form = load_form("FRM-01").empty_form()
        state = _state(form)
        asked: list[str] = []
        for _ in range(10):
            step = next_intake_step(state, [])
            if step["phase"] == "summary":
                break
            asked.extend(step["question_field_ids"])
            state.update(step)
            for field_id in step["question_field_ids"]:
                section, field = field_id.split(".", 1)
                if field in state["form"][section]:
                    state["form"][section][field] = "Provided"

        flags = [is_required_field(field_id) for field_id in asked]
        self.assertIn(True, flags)
        self.assertEqual(flags, sorted(flags, reverse=True), "an optional question came before a mandatory one")


if __name__ == "__main__":
    unittest.main()
