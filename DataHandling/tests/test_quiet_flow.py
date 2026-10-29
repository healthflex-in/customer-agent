"""In the normal flow the bot just moves on. It speaks up only when something unusual happened."""

import unittest

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.phrasing import VARIANTS


FORM = load_form("FRM-01")
ACK_STARTS = ("Thanks, I've noted", "Got it, I've written down", "Thanks, that tells me")
MAIN = "Piriformis tightness"
KNEE = "Sharp pain in left knee"


def state_for(form: dict, **extra) -> dict:
    return {
        "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
        "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": False,
        "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
        "noted_complaints": [], "revised_answers": [], "saved_answers": [], "saved_field_ids": [],
        "patient_tone": "neutral", **extra,
    }


def two_complaints_with_main_section_done() -> dict:
    form = FORM.empty_form()
    form["Present Complaint"].update({
        "Primary Complaint": MAIN, "Duration of the Issue": "A few days",
        "Onset (Gradual or Sudden)": "Gradual", "Mechanism of Injury or Cause": "Gym",
    })
    form["Additional Complaint 1"] = {
        "Primary Complaint": KNEE, "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
    }
    return form


def asked(*names, section="Present Complaint"):
    return [{
        "role": "agent", "message": "earlier",
        "question_field_ids": [f"{section}.{name}" for name in names],
    }]


def has_acknowledgement(text: str) -> bool:
    return text.startswith(ACK_STARTS)


class NormalFlowIsQuietTests(unittest.TestCase):
    def test_answering_everything_asked_goes_straight_to_the_next_section(self):
        form = two_complaints_with_main_section_done()
        state = state_for(
            form, saved_answers=["Duration of the Issue"],
            saved_field_ids=["Present Complaint.Duration of the Issue"],
        )

        step = next_intake_step(state, asked("Duration of the Issue"))

        section = step["question_field_ids"][0].split(".")[0]
        self.assertEqual(step["response_text"].split("\n")[0], FORM.section_intro(section))
        self.assertFalse(has_acknowledgement(step["response_text"]))

    def test_no_line_repeats_which_complaint_comes_first(self):
        form = two_complaints_with_main_section_done()

        step = next_intake_step(state_for(form), asked("Duration of the Issue"))

        self.assertNotIn("finish your", step["response_text"])
        self.assertNotIn("come back to", step["response_text"])

    def test_the_first_reply_after_the_opening_is_not_acknowledged(self):
        form = FORM.empty_form()
        form["Present Complaint"]["Primary Complaint"] = MAIN
        state = state_for(
            form, saved_answers=["Primary Complaint"], saved_field_ids=["Present Complaint.Primary Complaint"]
        )

        step = next_intake_step(state, [{"role": "agent", "message": "What is bothering you?"}])

        self.assertFalse(has_acknowledgement(step["response_text"]))

    def test_a_whole_run_of_full_answers_never_acknowledges(self):
        form = FORM.empty_form()
        history = [{"role": "agent", "message": "opening"}]
        for _ in range(6):
            step = next_intake_step(state_for(form), history)
            if step["phase"] == "summary":
                break
            ids = step["question_field_ids"]
            for field_id in ids:
                section, name = field_id.split(".", 1)
                if name in form[section]:
                    form[section][name] = "Provided"
            form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"
            history = step["history"]
            saved = [i.split(".", 1)[1] for i in ids]
            nxt = next_intake_step(state_for(form, saved_answers=saved, saved_field_ids=ids), history)
            self.assertFalse(has_acknowledgement(nxt["response_text"]), nxt["response_text"])
            history = nxt["history"]


class ItSpeaksUpWhenSomethingUnusualHappensTests(unittest.TestCase):
    def test_answering_only_part_of_the_batch_is_acknowledged(self):
        form = FORM.empty_form()
        form["Present Complaint"].update({"Primary Complaint": MAIN, "Duration of the Issue": "A few days"})
        names = list(form["Present Complaint"])
        state = state_for(
            form, saved_answers=["Duration of the Issue"],
            saved_field_ids=["Present Complaint.Duration of the Issue"],
        )

        step = next_intake_step(state, asked(*names))

        self.assertTrue(has_acknowledgement(step["response_text"]))
        self.assertIn("how long it's been going on", step["response_text"])

    def test_volunteering_something_nobody_asked_is_acknowledged(self):
        form = two_complaints_with_main_section_done()
        form["Pain Assessment"]["Severity (1-10)"] = "7/10"
        state = state_for(
            form,
            saved_answers=["Duration of the Issue", "Severity (1-10)"],
            saved_field_ids=["Present Complaint.Duration of the Issue", "Pain Assessment.Severity (1-10)"],
        )

        step = next_intake_step(state, asked("Duration of the Issue"))

        self.assertTrue(has_acknowledgement(step["response_text"]))
        self.assertIn("how strong the pain is", step["response_text"])

    def test_details_about_another_complaint_say_the_main_one_comes_first(self):
        form = two_complaints_with_main_section_done()
        form["Additional Complaint 1"]["Onset (Gradual or Sudden)"] = "Gradual"
        state = state_for(
            form, saved_answers=["Onset (Gradual or Sudden)"],
            saved_field_ids=["Additional Complaint 1.Onset (Gradual or Sudden)"],
        )

        step = next_intake_step(state, asked("Duration of the Issue"))

        self.assertIn("finish your piriformis tightness first", step["response_text"])

    def test_an_update_to_an_earlier_answer_is_still_acknowledged(self):
        form = two_complaints_with_main_section_done()
        state = state_for(form, revised_answers=[{"field": "Duration of the Issue", "value": "A week"}])

        step = next_intake_step(state, asked("Duration of the Issue"))

        self.assertIn("Duration of the Issue to A week", step["response_text"])

    def test_a_skip_is_still_acknowledged(self):
        form = two_complaints_with_main_section_done()

        step = next_intake_step(state_for(form, decline_notice=True), asked("Duration of the Issue"))

        self.assertTrue(any(line in step["response_text"] for line in VARIANTS["skip_accepted"]))


if __name__ == "__main__":
    unittest.main()
