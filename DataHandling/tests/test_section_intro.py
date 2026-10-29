"""The bot says when it moves on to a new section, and not for follow-ups inside the same one."""

import unittest

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.question_plan import question_for_missing_fields
from src.prompts import INITIAL_INTAKE_PROMPT


FORM = load_form("FRM-01")
FOLLOW_UP_HEADER = "Could you also tell me:"


def state_for(form: dict, **extra) -> dict:
    return {
        "form": form,
        "form_sections": list(form),
        "visit_context": "specific_complaint",
        "question_ask_counts": {},
        "tagged_form_template": None,
        "decline_notice": False,
        "mandatory_notice": False,
        "end_requested": False,
        "completion_percentage": 0,
        "noted_complaints": [],
        "revised_answers": [],
        "patient_tone": "neutral",
        **extra,
    }


def answered(form: dict, section: str, *fields: str) -> dict:
    for field in fields or list(form[section]):
        form[section][field] = "Provided"
    return form


class SectionIntroductionTests(unittest.TestCase):
    def test_the_form_has_an_introduction_for_every_section(self):
        for section in FORM.sections:
            with self.subTest(section=section):
                self.assertTrue(FORM.section_intro(section))

    def test_an_added_complaint_and_an_unknown_section_have_none(self):
        self.assertEqual(FORM.section_intro("Additional Complaint 1"), "")
        self.assertEqual(FORM.section_intro("Nope"), "")

    def test_the_first_questions_after_the_opening_dive_into_the_present_complaint(self):
        history = [{"role": "agent", "message": INITIAL_INTAKE_PROMPT}]

        step = next_intake_step(state_for(FORM.empty_form()), history)

        self.assertTrue(step["response_text"].startswith(FORM.section_intro("Present Complaint")))
        self.assertNotIn(FOLLOW_UP_HEADER, step["response_text"])
        self.assertIn("- How long have you been experiencing this issue?", step["response_text"])

    def test_leftover_questions_in_the_same_section_keep_the_follow_up_wording(self):
        form = answered(FORM.empty_form(), "Present Complaint", "Primary Complaint", "Duration of the Issue")
        history = [{
            "role": "agent", "message": "earlier batch",
            "question_field_ids": [f"Present Complaint.{name}" for name in form["Present Complaint"]],
        }]

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith(FOLLOW_UP_HEADER))
        self.assertNotIn(FORM.section_intro("Present Complaint"), step["response_text"])
        self.assertEqual(
            {field_id.split(".")[0] for field_id in step["question_field_ids"]}, {"Present Complaint"}
        )

    def test_a_new_section_gets_its_own_introduction(self):
        form = answered(FORM.empty_form(), "Present Complaint")
        history = [{
            "role": "agent", "message": "present complaint questions",
            "question_field_ids": [f"Present Complaint.{name}" for name in form["Present Complaint"]],
        }]

        step = next_intake_step(state_for(form), history)

        section = step["question_field_ids"][0].split(".")[0]
        self.assertNotEqual(section, "Present Complaint")
        self.assertTrue(step["response_text"].startswith(FORM.section_intro(section)))
        self.assertNotIn(FOLLOW_UP_HEADER, step["response_text"])

    def test_each_later_section_is_introduced_in_turn(self):
        form = FORM.empty_form()
        history = [{"role": "agent", "message": INITIAL_INTAKE_PROMPT}]
        introduced = []
        for _ in range(12):
            step = next_intake_step(state_for(form), history)
            if step["phase"] == "summary":
                break
            section = step["question_field_ids"][0].split(".")[0]
            if step["response_text"].startswith(FORM.section_intro(section)):
                introduced.append(section)
            history = step["history"]
            for field_id in step["question_field_ids"]:
                name_section, name = field_id.split(".", 1)
                if name in form[name_section]:
                    form[name_section][name] = "Provided"
            if "Current Lifestyle" in form["History & Diagnostics"]:
                form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"

        self.assertEqual(introduced, list(dict.fromkeys(introduced)), "a section was introduced twice")
        self.assertIn("Present Complaint", introduced)
        self.assertIn("Treatment Goals", introduced)

    def test_a_single_question_is_introduced_without_a_bullet_list(self):
        text = question_for_missing_fields(
            [("Pain Assessment", "Severity (1-10)")], FORM.field_labels, intro="Now the pain."
        )

        self.assertEqual(text, "Now the pain.\n\nOn a scale of 0 to 10, how severe is it right now?")

    def test_an_added_complaint_keeps_its_own_opening_line(self):
        form = answered(FORM.empty_form(), "Present Complaint")
        for section in ("Previous Consultations", "Pain Assessment", "History & Diagnostics", "Treatment Goals"):
            answered(form, section)
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"
        form["Additional Complaint 1"] = {
            "Primary Complaint": "Knee pain", "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
        }
        history = [{
            "role": "agent", "message": "goals", "question_field_ids": ["Treatment Goals.Short-Term Goals (within 3 months)"],
        }]

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith("Now about your other concern, Knee pain."))


if __name__ == "__main__":
    unittest.main()
