"""The model is told which problem each section is about, and which one Sage was asking about."""

import json
import unittest

from src.forms.loader import load_form
from src.graph.pure_functions.answer_mapping import complaint_labels, map_reply


MAIN = "Piriformis tightness"
KNEE = "Left knee pain"


def one_complaint_form() -> dict:
    form = load_form("FRM-01").empty_form()
    form["Present Complaint"]["Primary Complaint"] = MAIN
    return form


def two_complaint_form() -> dict:
    form = one_complaint_form()
    form["Additional Complaint 1"] = {
        "Primary Complaint": KNEE, "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "", "Severity (1-10)": "",
        "Aggravating Factors": "", "Relieving Factors": "",
    }
    return form


def prompt_for(form: dict, asked_ids=()) -> str:
    seen = []
    map_reply(
        reply="hello", last_question="How long?", form=form, form_sections=list(form),
        visit_context="specific_complaint", llm=lambda prompt: seen.append(prompt) or "{}",
        asked_ids=list(asked_ids),
    )
    return seen[0]


class ComplaintLabelTests(unittest.TestCase):
    def test_a_single_complaint_has_no_labels(self):
        self.assertEqual(complaint_labels(one_complaint_form()), {})

    def test_two_complaints_label_every_section(self):
        labels = complaint_labels(two_complaint_form())

        self.assertEqual(labels["Present Complaint"], MAIN)
        self.assertEqual(labels["Pain Assessment"], MAIN)
        self.assertEqual(labels["Previous Consultations"], MAIN)
        self.assertEqual(labels["Additional Complaint 1"], KNEE)
        self.assertNotIn("Treatment Goals", labels, "goals and history are about the patient, not one problem")

    def test_no_labels_until_the_main_complaint_is_known(self):
        form = two_complaint_form()
        form["Present Complaint"]["Primary Complaint"] = ""
        self.assertEqual(complaint_labels(form), {})

        form["Present Complaint"]["Primary Complaint"] = "Not applicable"
        self.assertEqual(complaint_labels(form), {})


class PromptTests(unittest.TestCase):
    def test_a_single_complaint_prompt_is_unchanged(self):
        prompt = prompt_for(one_complaint_form(), ["Present Complaint.Duration of the Issue"])

        self.assertNotIn("about the main complaint", prompt)
        self.assertNotIn("about the additional complaint", prompt)
        self.assertNotIn("(Sage's questions were about:", prompt)

    def test_with_two_complaints_the_main_fields_say_which_problem_they_are_about(self):
        prompt = prompt_for(two_complaint_form())

        self.assertIn(f"Duration of the Issue — How long have you been experiencing this issue? "
                      f"(about the main complaint: {MAIN})", prompt)
        self.assertIn(f"(about the additional complaint: {KNEE})", prompt)

    def test_the_model_is_told_which_problem_sage_was_asking_about(self):
        main_asked = prompt_for(two_complaint_form(), ["Present Complaint.Duration of the Issue"])
        knee_asked = prompt_for(two_complaint_form(), ["Additional Complaint 1.Duration of the Issue"])

        self.assertIn(f"(Sage's questions were about: {MAIN}.)", main_asked)
        self.assertIn(f"(Sage's questions were about: {KNEE}.)", knee_asked)

    def test_the_prompt_tells_the_model_to_follow_the_problem_the_patient_describes(self):
        prompt = prompt_for(two_complaint_form())

        self.assertIn("Put the facts in the problem the patient is describing", prompt)
        self.assertIn("use the problem Sage's questions were about", prompt)


class AnswersGoToTheComplaintDescribedTests(unittest.TestCase):
    def test_knee_answers_land_in_the_knee_section_and_leave_the_main_complaint_alone(self):
        form = two_complaint_form()
        payload = json.dumps({
            "visit_context": "specific_complaint",
            "answers": [
                {"field_id": "Additional Complaint 1.Onset (Gradual or Sudden)", "status": "answered",
                 "value": "Gradual", "detail": "", "correction": False},
                {"field_id": "Additional Complaint 1.Mechanism of Injury or Cause", "status": "answered",
                 "value": "Some exercise in the gym", "detail": "", "correction": False},
            ],
            "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
        })

        result = map_reply(
            reply="my knee pain started gradually, some gym exercise", last_question="How long?",
            form=form, form_sections=list(form), visit_context="specific_complaint",
            llm=lambda _prompt: payload, asked_ids=["Present Complaint.Duration of the Issue"],
        )

        self.assertEqual(result.form["Additional Complaint 1"]["Onset (Gradual or Sudden)"], "Gradual")
        self.assertEqual(
            result.form["Additional Complaint 1"]["Mechanism of Injury or Cause"], "Some exercise in the gym"
        )
        self.assertEqual(result.form["Present Complaint"]["Onset (Gradual or Sudden)"], "")
        self.assertEqual(result.form["Present Complaint"]["Mechanism of Injury or Cause"], "")


class LocationBelongsToTheRightComplaintTests(unittest.TestCase):
    LOCATION = "Pain Assessment.Primary Location of Pain"

    def _location(self, form, value, reply, asked=()):
        payload = json.dumps({
            "visit_context": "specific_complaint",
            "answers": [{"field_id": self.LOCATION, "status": "answered", "value": value,
                         "detail": "", "correction": False}],
            "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
        })
        result = map_reply(
            reply=reply, last_question="Where is the pain?", form=form, form_sections=list(form),
            visit_context="specific_complaint", llm=lambda _prompt: payload, asked_ids=list(asked),
            patient_text=reply,
        )
        return result.form["Pain Assessment"]["Primary Location of Pain"]

    def test_the_knees_area_is_not_saved_as_the_main_complaints_location(self):
        saved = self._location(
            two_complaint_form(), "Left knee", "my left knee hurts sometimes", asked=[self.LOCATION]
        )

        self.assertEqual(saved, "")

    def test_the_main_complaints_own_area_is_still_saved(self):
        saved = self._location(
            two_complaint_form(), "Buttock", "it is deep in my buttock", asked=[self.LOCATION]
        )

        self.assertEqual(saved, "Buttock")

    def test_with_one_complaint_nothing_is_held_back(self):
        saved = self._location(one_complaint_form(), "Left knee", "my left knee hurts", asked=[self.LOCATION])

        self.assertEqual(saved, "Left knee")


if __name__ == "__main__":
    unittest.main()
