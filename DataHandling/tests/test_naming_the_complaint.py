"""With more than one complaint, every question says which one it is about."""

import unittest

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.prompts import INITIAL_INTAKE_PROMPT


MAIN = "Piriformis tightness"
KNEE = "Left knee pain"
YOUR_MAIN = "your piriformis tightness"
YOUR_KNEE = "your left knee pain"
FORM = load_form("FRM-01")


def state_for(form: dict) -> dict:
    return {
        "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
        "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": False,
        "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
        "noted_complaints": [], "revised_answers": [], "saved_answers": [], "patient_tone": "neutral",
    }


def form_with_main(*, with_knee: bool) -> dict:
    form = FORM.empty_form()
    form["Present Complaint"]["Primary Complaint"] = MAIN
    if with_knee:
        form["Additional Complaint 1"] = {
            "Primary Complaint": KNEE, "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
        }
    return form


def asked_before(*ids):
    return [{"role": "agent", "message": "earlier", "question_field_ids": list(ids)}]


OPENING = [{"role": "agent", "message": INITIAL_INTAKE_PROMPT}]


class FocusSentenceTests(unittest.TestCase):
    def test_a_section_change_does_not_repeat_which_complaint_comes_first(self):
        step = next_intake_step(state_for(form_with_main(with_knee=True)), OPENING)

        self.assertEqual(step["response_text"].split("\n")[0], FORM.section_intro("Present Complaint"))
        self.assertNotIn("finish", step["response_text"])
        self.assertNotIn("come back to", step["response_text"])

    def test_with_one_complaint_nothing_is_added(self):
        step = next_intake_step(state_for(form_with_main(with_knee=False)), OPENING)

        self.assertNotIn("come back to", step["response_text"])
        self.assertNotIn("About ", step["response_text"])
        self.assertNotIn(MAIN, step["response_text"])

    def test_a_later_main_section_is_introduced_without_the_finish_first_line(self):
        form = form_with_main(with_knee=True)
        for name in form["Present Complaint"]:
            form["Present Complaint"][name] = "Provided"
        form["Present Complaint"]["Primary Complaint"] = MAIN
        history = asked_before(*[f"Present Complaint.{name}" for name in form["Present Complaint"]])

        step = next_intake_step(state_for(form), history)

        section = step["question_field_ids"][0].split(".")[0]
        self.assertIn(section, ("Previous Consultations", "Pain Assessment"))
        self.assertTrue(step["response_text"].startswith(FORM.section_intro(section)))
        self.assertNotIn("finish", step["response_text"])

    def test_history_and_goals_are_about_the_patient_so_they_name_no_complaint(self):
        form = form_with_main(with_knee=True)
        for section in ("Present Complaint", "Previous Consultations", "Pain Assessment"):
            for name in form[section]:
                form[section][name] = "Provided"
        form["Present Complaint"]["Primary Complaint"] = MAIN
        history = asked_before("Pain Assessment.Severity (1-10)")

        step = next_intake_step(state_for(form), history)

        self.assertEqual(step["question_field_ids"][0].split(".")[0], "History & Diagnostics")
        self.assertNotIn(MAIN, step["response_text"])
        self.assertNotIn("come back to", step["response_text"])


class FollowUpWordingTests(unittest.TestCase):
    def test_a_follow_up_in_the_same_section_names_the_complaint(self):
        form = form_with_main(with_knee=True)
        form["Present Complaint"]["Duration of the Issue"] = "Provided"
        history = asked_before(*[f"Present Complaint.{name}" for name in form["Present Complaint"]])

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith(f"Could you also tell me about {YOUR_MAIN}:"))

    def test_a_single_leftover_question_is_introduced_with_the_complaint(self):
        form = form_with_main(with_knee=True)
        for name in ("Duration of the Issue", "Onset (Gradual or Sudden)"):
            form["Present Complaint"][name] = "Provided"
        history = asked_before(*[f"Present Complaint.{name}" for name in form["Present Complaint"]])

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith(f"About {YOUR_MAIN}: What caused it"))

    def test_with_one_complaint_a_follow_up_keeps_the_old_wording(self):
        form = form_with_main(with_knee=False)
        form["Present Complaint"]["Duration of the Issue"] = "Provided"
        history = asked_before(*[f"Present Complaint.{name}" for name in form["Present Complaint"]])

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith("Could you also tell me:"))

    def test_an_added_complaints_own_batch_keeps_its_opening_line(self):
        form = form_with_main(with_knee=True)
        for section in FORM.sections:
            for name in form[section]:
                form[section][name] = "Provided"
        form["Present Complaint"]["Primary Complaint"] = MAIN
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"
        history = asked_before("Treatment Goals.Short-Term Goals (within 3 months)")

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["response_text"].startswith(f"Now about your other concern, {KNEE}."))
        self.assertNotIn("About ", step["response_text"])

    def test_the_stored_question_text_names_the_complaint_for_boundary_quotes(self):
        form = form_with_main(with_knee=True)
        form["Present Complaint"]["Duration of the Issue"] = "Provided"
        history = asked_before(*[f"Present Complaint.{name}" for name in form["Present Complaint"]])

        step = next_intake_step(state_for(form), history)

        self.assertTrue(step["history"][-1]["question_text"].startswith(f"Could you also tell me about {YOUR_MAIN}:"))


if __name__ == "__main__":
    unittest.main()
