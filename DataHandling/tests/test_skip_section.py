"""Skipping a whole added complaint: "Alright", every question closed, the complaint's name kept."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.additional_complaint import skip_section_target
from src.graph.pure_functions.phrasing import VARIANTS
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


KNEE = "left knee pain"
NECK = "Neck stiffness"
SKIPPED = "Skipped by patient"
ASKED_KNEE = ["Additional Complaint 1.Duration of the Issue", "Additional Complaint 1.Onset (Gradual or Sudden)"]
ASKED_MAIN = ["Present Complaint.Duration of the Issue"]


def added(label):
    return {
        "Primary Complaint": label, "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "", "Severity (1-10)": "", "Aggravating Factors": "", "Relieving Factors": "",
    }


def form_with(*labels):
    form = load_form("FRM-01").empty_form()
    form["Present Complaint"]["Primary Complaint"] = "Piriformis tightness"
    for number, label in enumerate(labels, 1):
        form[f"Additional Complaint {number}"] = added(label)
    return form


class TargetTests(unittest.TestCase):
    def test_skip_all_or_skip_this_complaint_while_an_added_complaint_is_asked(self):
        for text in ("skip all", "skip this complaint", "skip the whole section", "skip everything"):
            with self.subTest(text=text):
                self.assertEqual(skip_section_target(text, form_with(KNEE), ASKED_KNEE), "Additional Complaint 1")

    def test_naming_the_complaint_works_from_anywhere(self):
        self.assertEqual(skip_section_target("skip the knee questions", form_with(KNEE), ASKED_MAIN), "Additional Complaint 1")
        self.assertEqual(skip_section_target("skip the neck", form_with(KNEE, NECK), ASKED_MAIN), "Additional Complaint 2")

    def test_the_other_complaint_works_when_there_is_exactly_one(self):
        self.assertEqual(skip_section_target("skip the other complaint", form_with(KNEE), ASKED_MAIN), "Additional Complaint 1")

    def test_it_never_guesses(self):
        cases = (
            ("skip", form_with(KNEE), ASKED_KNEE),                       # a bare skip asks which question
            ("skip all", form_with(KNEE), ASKED_MAIN),                    # skip all in the main complaint is unchanged
            ("skip these questions", form_with(KNEE), ASKED_MAIN),        # about the main complaint's questions
            ("skip the other complaint", form_with(KNEE, NECK), ASKED_MAIN),  # two candidates
            ("I also have a sore knee", form_with(KNEE), ASKED_KNEE),     # no skip at all
            ("skip all", load_form("FRM-01").empty_form(), ASKED_MAIN),   # no added complaint
        )
        for text, form, asked in cases:
            with self.subTest(text=text, asked=asked[0]):
                self.assertIsNone(skip_section_target(text, form, asked))


def run(state, payload=None):
    payload = payload or json.dumps({
        "visit_context": "specific_complaint", "answers": [], "complaints": [], "decline_scope": "unspecified",
        "reports": {"has_reports": None, "wants_upload": None}, "tone": "neutral", "intent": "skip",
    })
    with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _e: None):
        return make_extract_node(lambda _p: payload, reasoning_llm=lambda _p: payload)(state)


def state_for(user_input, form, asked, current):
    state = get_fresh_interview_state("u", "FRM-01", "s")
    state.update(
        phase="interviewing", visit_context="specific_complaint", form=form, form_sections=list(form),
        current_section=current, user_input=user_input,
        history=[{"role": "agent", "message": "q", "question_field_ids": list(asked)}],
    )
    return state


class SkipWholeSectionTests(unittest.TestCase):
    def test_every_open_question_is_closed_and_the_complaint_name_stays(self):
        form = form_with(KNEE, NECK)

        result = run(state_for("skip all", form, ASKED_KNEE, "Additional Complaint 1"))

        knee = result["form"]["Additional Complaint 1"]
        self.assertEqual(knee["Primary Complaint"], KNEE)
        for name in ("Duration of the Issue", "Onset (Gradual or Sudden)", "Mechanism of Injury or Cause",
                     "Severity (1-10)", "Aggravating Factors", "Relieving Factors"):
            self.assertEqual(knee[name], SKIPPED, name)
        self.assertTrue(result["section_skipped"])

    def test_the_other_complaints_and_the_main_complaint_are_untouched(self):
        form = form_with(KNEE, NECK)

        result = run(state_for("skip all", form, ASKED_KNEE, "Additional Complaint 1"))

        self.assertEqual(result["form"]["Additional Complaint 2"], added(NECK))
        self.assertEqual(result["form"]["Present Complaint"]["Duration of the Issue"], "")

    def test_the_reply_is_alright_and_then_the_next_questions(self):
        form = form_with(KNEE, NECK)
        for section in load_form("FRM-01").sections:
            for name in form[section]:
                form[section][name] = "Provided"
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"
        state = state_for("skip all", form, ASKED_KNEE, "Additional Complaint 1")
        result = run(state)

        step = next_intake_step({**state, **result}, result["history"])

        self.assertTrue(any(step["response_text"].startswith(line) for line in VARIANTS["section_skipped"]))
        self.assertNotIn("That's completely okay", step["response_text"])
        self.assertNotIn("Sorry", step["response_text"])
        self.assertIn(NECK, step["response_text"])
        self.assertEqual({i.split(".")[0] for i in step["question_field_ids"]}, {"Additional Complaint 2"})

    def test_naming_the_complaint_skips_it_while_the_main_complaint_is_asked(self):
        form = form_with(KNEE)

        result = run(state_for("skip the knee questions", form, ASKED_MAIN, "Present Complaint"))

        self.assertEqual(result["form"]["Additional Complaint 1"]["Duration of the Issue"], SKIPPED)
        self.assertEqual(result["form"]["Present Complaint"]["Duration of the Issue"], "")

    def test_with_nothing_else_to_ask_the_summary_follows_the_alright(self):
        form = form_with(KNEE)
        for section in load_form("FRM-01").sections:
            for name in form[section]:
                form[section][name] = "Provided"
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Teacher; Activity/exercise: Walking"
        state = state_for("skip all", form, ASKED_KNEE, "Additional Complaint 1")
        result = run(state)

        step = next_intake_step({**state, **result}, result["history"])

        self.assertEqual(step["phase"], "summary")
        self.assertTrue(any(step["response_text"].startswith(line) for line in VARIANTS["section_skipped"]))
        self.assertIn(f"Primary Complaint: {KNEE}", step["response_text"])


class OtherSkipBehaviourIsUnchangedTests(unittest.TestCase):
    def test_a_bare_skip_with_several_questions_open_still_asks_which(self):
        result = run(state_for("skip", form_with(KNEE), ASKED_KNEE, "Additional Complaint 1"))

        self.assertIn("Which of these would you like to skip?", result["response_text"])
        self.assertFalse(result.get("section_skipped", False))

    def test_skip_all_in_the_main_complaint_still_leaves_the_added_complaint_alone(self):
        form = form_with(KNEE)
        asked = ["Pain Assessment.Relieving Factors", "History & Diagnostics.Reports"]

        result = run(state_for("skip all", form, asked, "Pain Assessment"))

        self.assertEqual(result["form"]["Pain Assessment"]["Relieving Factors"], SKIPPED)
        self.assertEqual(result["form"]["Additional Complaint 1"], added(KNEE))
        self.assertFalse(result["section_skipped"])

    def test_skip_this_style_phrases_keep_the_name_and_skip_the_rest(self):
        for text in ("skip this", "lets skip this", "skip it", "no need"):
            with self.subTest(text=text):
                result = run(state_for(text, form_with(KNEE), ASKED_KNEE, "Additional Complaint 1"))

                knee = result["form"]["Additional Complaint 1"]
                self.assertEqual(knee["Primary Complaint"], KNEE)
                self.assertEqual(knee["Duration of the Issue"], SKIPPED)

    def test_an_explicit_removal_or_a_mistake_still_removes_the_complaint(self):
        for text in ("remove this complaint", "there is no additional complaint", "no need by mistake i told you"):
            with self.subTest(text=text):
                result = run(state_for(text, form_with(KNEE), ASKED_KNEE, "Additional Complaint 1"))

                self.assertNotIn("Additional Complaint 1", result["form"])


if __name__ == "__main__":
    unittest.main()
