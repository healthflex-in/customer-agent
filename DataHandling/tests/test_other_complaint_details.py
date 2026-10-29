"""Details about another complaint are saved, acknowledged, and the main complaint still comes first."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.phrasing import VARIANTS, your as _your
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.extract import make_extract_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    config = types.ModuleType("langgraph.config")
    config.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
    sys.modules["langgraph.config"] = config
    from src.graph.nodes.extract import make_extract_node


MAIN = "Piriformis tightness"
KNEE = "Left knee pain"
KNEE_ONSET = "Additional Complaint 1.Onset (Gradual or Sudden)"
KNEE_MECHANISM = "Additional Complaint 1.Mechanism of Injury or Cause"


def two_complaints() -> dict:
    form = load_form("FRM-01").empty_form()
    form["Present Complaint"]["Primary Complaint"] = MAIN
    form["Additional Complaint 1"] = {
        "Primary Complaint": KNEE, "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "", "Severity (1-10)": "",
        "Aggravating Factors": "", "Relieving Factors": "",
    }
    return form


def state_for(form: dict, **extra) -> dict:
    return {
        "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
        "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": False,
        "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
        "noted_complaints": [], "revised_answers": [], "saved_answers": [], "saved_field_ids": [],
        "patient_tone": "neutral", **extra,
    }


ASKED_MAIN = [{
    "role": "agent", "message": "earlier",
    "question_field_ids": ["Present Complaint.Duration of the Issue", "Present Complaint.Onset (Gradual or Sudden)"],
}]


def note_variants(things):
    return [
        line.format(things=things, complaint="your left knee pain", active="your piriformis tightness")
        for line in VARIANTS["other_complaint_saved"]
    ]


class AcknowledgingAnotherComplaintTests(unittest.TestCase):
    def _step(self, **extra):
        form = two_complaints()
        form["Additional Complaint 1"]["Onset (Gradual or Sudden)"] = "Gradual"
        form["Additional Complaint 1"]["Mechanism of Injury or Cause"] = "Some exercise in the gym"
        state = state_for(form, **extra)
        return next_intake_step(state, ASKED_MAIN)

    def test_the_details_are_acknowledged_and_the_main_complaint_comes_first(self):
        step = self._step(
            saved_field_ids=[KNEE_ONSET, KNEE_MECHANISM],
            saved_answers=["Onset (Gradual or Sudden)", "Mechanism of Injury or Cause"],
        )

        wanted = note_variants("how it started and what may have caused it")
        self.assertTrue(any(step["response_text"].startswith(line) for line in wanted), step["response_text"])
        self.assertNotIn("Sorry", step["response_text"])
        self.assertTrue(all(
            field_id.startswith(("Present Complaint", "Previous Consultations", "Pain Assessment"))
            for field_id in step["question_field_ids"]
        ))

    def test_the_main_complaint_is_named_once_not_twice(self):
        step = self._step(saved_field_ids=[KNEE_ONSET], saved_answers=["Onset (Gradual or Sudden)"])

        self.assertEqual(step["response_text"].count("finish your piriformis tightness first"), 1)

    def test_a_field_with_no_phrase_is_still_acknowledged_plainly(self):
        step = self._step(
            saved_field_ids=["Additional Complaint 1.Some Field The Form Does Not Describe"],
            saved_answers=["Some Field The Form Does Not Describe"],
        )

        self.assertTrue(any(
            step["response_text"].startswith(line) for line in note_variants("what you told me")
        ))

    def test_an_update_to_an_earlier_answer_takes_priority(self):
        step = self._step(
            saved_field_ids=[KNEE_ONSET],
            revised_answers=[{"field": "Severity (1-10)", "value": "6/10"}],
        )

        self.assertIn("Severity to 6/10", step["response_text"])
        self.assertNotIn("for your left knee pain", step["response_text"])

    def test_a_newly_noted_complaint_takes_priority(self):
        step = self._step(saved_field_ids=[KNEE_ONSET], noted_complaints=["Shoulder pain"])

        self.assertIn("Shoulder pain", step["response_text"].split("\n\n")[0])
        self.assertNotIn("for your left knee pain", step["response_text"])

    def test_nothing_special_is_said_when_the_details_belong_to_the_complaint_being_asked(self):
        form = two_complaints()
        history = [{"role": "agent", "message": "knee", "question_field_ids": ["Additional Complaint 1.Duration of the Issue"]}]
        for section in load_form("FRM-01").sections:
            for name in form[section]:
                form[section][name] = "Provided"
        form["Present Complaint"]["Primary Complaint"] = MAIN
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"

        step = next_intake_step(
            state_for(form, saved_field_ids=[KNEE_ONSET], saved_answers=["Onset (Gradual or Sudden)"]), history
        )

        self.assertNotIn("finish your piriformis tightness first", step["response_text"])
        self.assertNotIn("come back to it", step["response_text"])

    def test_with_one_complaint_there_is_no_other_complaint_note(self):
        form = load_form("FRM-01").empty_form()
        form["Present Complaint"]["Primary Complaint"] = MAIN

        step = next_intake_step(
            state_for(form, saved_field_ids=[KNEE_ONSET], saved_answers=["Onset (Gradual or Sudden)"]), ASKED_MAIN
        )

        self.assertNotIn("come back to it", step["response_text"])
        self.assertNotIn("for your", step["response_text"])


class WholeTurnTests(unittest.TestCase):
    """The patient talks about the knee while asked about the main complaint."""

    def test_knee_details_are_saved_acknowledged_and_the_main_questions_follow(self):
        form = two_complaints()
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing", visit_context="specific_complaint", form=form,
            form_sections=list(form), current_section="Present Complaint",
            user_input="my knee pain started gradually, maybe some exercise at the gym",
            history=ASKED_MAIN,
        )
        payload = json.dumps({
            "visit_context": "specific_complaint",
            "answers": [
                {"field_id": KNEE_ONSET, "status": "answered", "value": "Gradual", "detail": "", "correction": False},
                {"field_id": KNEE_MECHANISM, "status": "answered", "value": "Some exercise at the gym",
                 "detail": "", "correction": False},
            ],
            "reports": {"has_reports": None, "wants_upload": None}, "complaints": [KNEE],
        })
        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(lambda _prompt: "{}", reasoning_llm=lambda _prompt: payload)(state)

        self.assertEqual(result["form"]["Additional Complaint 1"]["Onset (Gradual or Sudden)"], "Gradual")
        self.assertEqual(result["form"]["Present Complaint"]["Onset (Gradual or Sudden)"], "")
        self.assertEqual(result["noted_complaints"], [], "the knee was already noted, so it is not new")
        self.assertEqual(set(result["saved_field_ids"]), {KNEE_ONSET, KNEE_MECHANISM})

        step = next_intake_step({**state, **result}, result["history"])

        self.assertIn("for your left knee pain", step["response_text"])
        self.assertIn("finish your piriformis tightness first", step["response_text"])
        self.assertIn("How long have you been experiencing this issue?", step["response_text"])


class ComplaintPhraseTests(unittest.TestCase):
    def test_a_complaint_is_said_the_way_a_patient_would(self):
        self.assertEqual(_your("Piriformis tightness"), "your piriformis tightness")
        self.assertEqual(_your("  Left knee pain "), "your left knee pain")

    def test_an_acronym_keeps_its_capitals(self):
        self.assertEqual(_your("ACL injury"), "your ACL injury")


if __name__ == "__main__":
    unittest.main()
