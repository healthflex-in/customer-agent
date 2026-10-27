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
from src.graph.pure_functions.answer_mapping import DECLINED
from src.graph.pure_functions.question_plan import CLINICIAN_LATER, NOTED_COMPLAINT, REFRAME_QUESTIONS


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


def filled_except(field_id):
    state = get_fresh_interview_state("u", "FRM-01", "s")
    for fields in state["form"].values():
        for name in fields:
            fields[name] = "Provided"
    state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
        "Work: Accountant; Activity/exercise: Walking"
    )
    section, name = field_id.split(".", 1)
    state["form"][section][name] = ""
    state["visit_context"] = "specific_complaint"
    state["phase"] = "interviewing"
    return state


class SkipQuestionTests(unittest.TestCase):
    def test_first_skip_asks_a_shorter_question_instead_of_the_same_one(self):
        field_id = "Pain Assessment.Severity (1-10)"
        state = filled_except(field_id)
        state["user_input"] = "skip this"
        state["history"] = [{
            "role": "agent",
            "message": "On a scale of 0 to 10, how severe is it right now?",
            "question_field_ids": [field_id],
        }]
        result = run(state, FakeModel(mappings={"skip this": mapping(answers=[{
            "field_id": field_id, "status": "declined", "value": "", "detail": "", "correction": False,
        }])}))

        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "")
        self.assertEqual(result["skip_reframe_ids"], [field_id])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertIn(REFRAME_QUESTIONS["Severity (1-10)"], step["response_text"])
        self.assertNotIn("how severe is it right now", step["response_text"])

    def test_second_skip_is_saved_as_declined_and_the_interview_moves_on(self):
        field_id = "Pain Assessment.Severity (1-10)"
        state = filled_except(field_id)
        state["skip_reframed_ids"] = [field_id]
        state["user_input"] = "skip"
        state["history"] = [{
            "role": "agent",
            "message": REFRAME_QUESTIONS["Severity (1-10)"],
            "question_field_ids": [field_id],
        }]
        result = run(state, FakeModel(mappings={"skip": mapping(answers=[{
            "field_id": field_id, "status": "declined", "value": "", "detail": "", "correction": False,
        }])}))

        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], DECLINED)
        self.assertTrue(result["decline_notice"])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertEqual(step["phase"], "summary")
        self.assertIn(CLINICIAN_LATER, step["response_text"])
        self.assertNotIn(REFRAME_QUESTIONS["Severity (1-10)"], step["response_text"])

    def test_vague_skip_of_several_questions_asks_which_one(self):
        asked = [
            "Pain Assessment.Severity (1-10)",
            "Pain Assessment.Aggravating Factors",
            "Pain Assessment.Relieving Factors",
            "History & Diagnostics.Reports",
        ]
        state = filled_except(asked[0])
        state["user_input"] = "skip"
        state["history"] = [{"role": "agent", "message": "Could you also tell me:", "question_field_ids": asked}]
        result = run(state, FakeModel(mappings={"skip": {
            **mapping(),
            "decline_scope": "unspecified",
        }}))

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("Which of these would you like to skip?", result["response_text"])
        self.assertEqual(result["history"][-1]["skip_question_ids"], asked)


class LaterComplaintTests(unittest.TestCase):
    def test_a_new_problem_mid_interview_is_kept_without_changing_the_main_one(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            visit_context="specific_complaint",
            user_input="My knee hurts as well, about 8 out of 10",
            history=[{"role": "agent", "message": "How severe is the back pain?"}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Lumbar facet joint pain"
        result = run(state, FakeModel(mappings={state["user_input"]: mapping(
            answers=[answer("Pain Assessment.Severity (1-10)", "8")],
            complaints=["Knee pain"],
        )}))

        self.assertEqual(result["form"]["Present Complaint"]["Primary Complaint"], "Lumbar facet joint pain")
        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "")
        self.assertEqual(result["form"]["Additional Complaint 1"]["Primary Complaint"], "Knee pain")
        self.assertEqual(result["noted_complaints"], ["Knee pain"])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertIn(NOTED_COMPLAINT.format(labels="Knee pain"), step["response_text"])
        self.assertNotIn("Now about your other concern", step["response_text"])

    def test_pain_spreading_from_one_problem_does_not_add_a_complaint(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            visit_context="specific_complaint",
            user_input="The pain goes down my right leg and it is 6 out of 10",
            history=[{"role": "agent", "message": "How severe is it?"}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Lumbar radiculopathy"
        result = run(state, FakeModel(mappings={state["user_input"]: mapping(
            answers=[answer("Pain Assessment.Severity (1-10)", "6")],
            complaints=["Lumbar radiculopathy"],
        )}))

        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "6/10")
        self.assertNotIn("Additional Complaint 1", result["form"])
        self.assertEqual(result["noted_complaints"], [])


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
