import json
import sys
import types
import unittest
from unittest.mock import patch

from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.extract import make_extract_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    langgraph_config_module = types.ModuleType("langgraph.config")
    langgraph_config_module.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
    sys.modules["langgraph.config"] = langgraph_config_module
    from src.graph.nodes.extract import make_extract_node

from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step


FIRST_REPLY = (
    "I have lumbar facet joint pain for three months and plantar fasciopathy "
    "in my left foot for two weeks. I work as an accountant."
)
COMPLAINTS = ["Lumbar facet joint pain", "Left plantar fasciopathy"]


class FakeModel:
    """Answers the focus prompt and the mapping prompt like the real model would."""

    def __init__(self, choice=None, mappings=None):
        self.choice = choice
        self.mappings = mappings or {}
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "Sage asked which one they would like to focus on" in prompt:
            return json.dumps({"choice": self.choice})
        for reply, payload in self.mappings.items():
            if f"The patient replied:\n{reply}" in prompt:
                return json.dumps(payload)
        return json.dumps({"visit_context": "specific_complaint", "answers": [], "complaints": []})


def mapping(answers=(), complaints=()):
    return {
        "visit_context": "specific_complaint",
        "answers": list(answers),
        "reports": {"has_reports": None, "wants_upload": None},
        "complaints": list(complaints),
    }


def answer(field_id, value):
    return {"field_id": field_id, "status": "answered", "value": value, "detail": "", "correction": False}


def run(state, model):
    with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
        return make_extract_node(lambda _prompt: "{}", reasoning_llm=model)(state)


def first_turn_state():
    state = get_fresh_interview_state("u", "FRM-01", "s")
    state.update(
        phase="interviewing",
        current_section="Present Complaint",
        user_input=FIRST_REPLY,
        history=[{"role": "agent", "message": "What brings you in today?"}],
    )
    return state


FIRST_MAPPING = mapping(
    answers=[
        answer("Present Complaint.Primary Complaint", "Lumbar facet joint pain and plantar fasciopathy"),
        answer("Present Complaint.Duration of the Issue", "Three months"),
        answer("History & Diagnostics.Current Lifestyle — Work", "Accountant"),
    ],
    complaints=COMPLAINTS,
)


def focus_turn_state(reply, times_asked=1):
    first = run(first_turn_state(), FakeModel(mappings={FIRST_REPLY: FIRST_MAPPING}))
    state = first_turn_state()
    state.update(
        form=first["form"],
        history=first["history"],
        question_ask_counts=first["question_ask_counts"],
        user_input=reply,
    )
    state["question_ask_counts"]["Present Complaint.Primary Complaint"] = times_asked
    return state


class ComplaintFocusTests(unittest.TestCase):
    def test_several_complaints_ask_which_one_to_focus_on(self):
        result = run(first_turn_state(), FakeModel(mappings={FIRST_REPLY: FIRST_MAPPING}))

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("Which one would you like to focus on first?", result["response_text"])
        for complaint in COMPLAINTS:
            self.assertIn(complaint, result["response_text"])
        self.assertEqual(result["history"][-1]["focus_complaints"], COMPLAINTS)
        self.assertEqual(result["form"]["Present Complaint"]["Primary Complaint"], "")
        self.assertEqual(result["form"]["Present Complaint"]["Duration of the Issue"], "")
        self.assertEqual(
            result["form"]["History & Diagnostics"]["Current Lifestyle"], "Work: Accountant"
        )

    def test_one_complaint_does_not_ask_for_focus(self):
        single = mapping(
            answers=[answer("Present Complaint.Primary Complaint", "Lumbar facet joint pain")],
            complaints=["Lumbar facet joint pain"],
        )
        result = run(first_turn_state(), FakeModel(mappings={FIRST_REPLY: single}))

        self.assertNotIn("direct_response_handled", result)
        self.assertEqual(
            result["form"]["Present Complaint"]["Primary Complaint"], "Lumbar facet joint pain"
        )

    def test_chosen_complaint_becomes_primary_and_the_other_is_kept(self):
        state = focus_turn_state("The foot one")
        remapped = mapping(answers=[
            answer("Present Complaint.Duration of the Issue", "Two weeks"),
            answer("Additional Complaint 1.Duration of the Issue", "Three months"),
        ])
        model = FakeModel(choice=2, mappings={FIRST_REPLY: remapped})

        result = run(state, model)

        form = result["form"]
        self.assertEqual(form["Present Complaint"]["Primary Complaint"], "Left plantar fasciopathy")
        self.assertEqual(form["Present Complaint"]["Duration of the Issue"], "Two weeks")
        self.assertEqual(form["Additional Complaint 1"]["Primary Complaint"], "Lumbar facet joint pain")
        self.assertEqual(form["Additional Complaint 1"]["Duration of the Issue"], "Three months")
        self.assertIn("Additional Complaint 1", result["form_sections"])
        self.assertNotIn("direct_response_handled", result)

    def test_unclear_choice_is_asked_again(self):
        result = run(focus_turn_state("Both are bad"), FakeModel(choice=None))

        self.assertIn("Which one would you like to focus on first?", result["response_text"])
        self.assertEqual(result["question_ask_counts"]["Present Complaint.Primary Complaint"], 2)
        self.assertEqual(result["form"]["Present Complaint"]["Primary Complaint"], "")

    def test_unclear_choice_at_the_limit_takes_the_first_complaint(self):
        state = focus_turn_state("Both are bad", times_asked=MAX_TIMES_ASKED)

        result = run(state, FakeModel(choice=None))

        self.assertEqual(
            result["form"]["Present Complaint"]["Primary Complaint"], "Lumbar facet joint pain"
        )
        self.assertEqual(
            result["form"]["Additional Complaint 1"]["Primary Complaint"], "Left plantar fasciopathy"
        )


class AdditionalComplaintQuestionTests(unittest.TestCase):
    def test_other_complaint_is_asked_after_the_main_one_and_named(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        for fields in state["form"].values():
            for name in fields:
                fields[name] = "Provided"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: Accountant; Activity/exercise: Walking"
        )
        state["form"]["Treatment Goals"]["Long-Term Goals (after 3 months)"] = ""
        state["form"]["Additional Complaint 1"] = {
            "Primary Complaint": "Left plantar fasciopathy",
            "Duration of the Issue": "",
            "Onset (Gradual or Sudden)": "",
        }
        state["form_sections"] = list(state["form"])
        state["visit_context"] = "specific_complaint"

        main = next_intake_step(state, [])
        state["form"]["Treatment Goals"]["Long-Term Goals (after 3 months)"] = "Run again"
        other = next_intake_step(state, [])

        self.assertEqual(main["question_field_ids"], ["Treatment Goals.Long-Term Goals (after 3 months)"])
        self.assertTrue(
            other["response_text"].startswith("Now about your other concern, Left plantar fasciopathy.")
        )
        self.assertEqual(
            other["question_field_ids"],
            [
                "Additional Complaint 1.Duration of the Issue",
                "Additional Complaint 1.Onset (Gradual or Sudden)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
