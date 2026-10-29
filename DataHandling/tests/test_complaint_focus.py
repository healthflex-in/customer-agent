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
from src.graph.pure_functions.phrasing import VARIANTS
from src.graph.pure_functions.question_plan import (
    END_CONFIRM,
    END_CONTINUE,
    END_NOT_YET_REQUIRED,
    MANDATORY_NOTICE,
)


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


def filled_except(*field_ids):
    state = get_fresh_interview_state("u", "FRM-01", "s")
    for fields in state["form"].values():
        for name in fields:
            fields[name] = "Provided"
    state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
        "Work: Accountant; Activity/exercise: Walking"
    )
    for field_id in field_ids:
        section, name = field_id.split(".", 1)
        state["form"][section][name] = ""
    state["visit_context"] = "specific_complaint"
    state["phase"] = "interviewing"
    return state


class SkipQuestionTests(unittest.TestCase):
    def test_skip_saves_the_field_as_declined_and_the_interview_moves_on(self):
        field_id = "Pain Assessment.Relieving Factors"
        state = filled_except(field_id)
        state["user_input"] = "skip this"
        state["history"] = [{
            "role": "agent",
            "message": "What makes it feel better or gives relief?",
            "question_field_ids": [field_id],
        }]
        result = run(state, FakeModel(mappings={"skip this": mapping(answers=[{
            "field_id": field_id, "status": "declined", "value": "", "detail": "", "correction": False,
        }])}))

        self.assertEqual(result["form"]["Pain Assessment"]["Relieving Factors"], DECLINED)
        self.assertTrue(result["decline_notice"])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertEqual(step["phase"], "summary")
        self.assertTrue(any(text in step["response_text"] for text in VARIANTS["skip_accepted"]))
        self.assertNotIn("what makes it feel better", step["response_text"].lower())
        self.assertIn("Relieving Factors: Skipped by patient", step["response_text"])
        self.assertNotIn("Declined", step["response_text"])

    def test_mandatory_question_cannot_be_skipped(self):
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
        self.assertFalse(result["decline_notice"])
        self.assertTrue(result["mandatory_notice"])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertEqual(step["question_field_ids"], [field_id])
        self.assertIn(MANDATORY_NOTICE, step["response_text"])

    def test_vague_skip_of_several_questions_asks_which_one(self):
        asked = [
            "Pain Assessment.Relieving Factors",
            "History & Diagnostics.Reports",
            "Treatment Goals.Short-Term Goals (within 3 months)",
        ]
        state = filled_except(*asked)
        state["user_input"] = "skip"
        state["history"] = [{"role": "agent", "message": "Could you also tell me:", "question_field_ids": asked}]
        result = run(state, FakeModel(mappings={"skip": {
            **mapping(),
            "decline_scope": "unspecified",
        }}))

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("Which of these would you like to skip?", result["response_text"])
        self.assertEqual(result["history"][-1]["skip_question_ids"], asked)

    def test_skip_prompt_leaves_out_mandatory_questions(self):
        mandatory = [
            "Pain Assessment.Severity (1-10)",
            "Pain Assessment.Aggravating Factors",
        ]
        optional = ["Pain Assessment.Relieving Factors", "History & Diagnostics.Reports"]
        state = filled_except(*mandatory, *optional)
        state["user_input"] = "skip"
        state["history"] = [{
            "role": "agent",
            "message": "Could you also tell me:",
            "question_field_ids": mandatory + optional,
        }]
        result = run(state, FakeModel(mappings={"skip": {
            **mapping(),
            "decline_scope": "unspecified",
        }}))

        self.assertEqual(result["history"][-1]["skip_question_ids"], optional)


class GoodbyeTests(unittest.TestCase):
    def _asked_severity(self, user_input):
        field_id = "Pain Assessment.Severity (1-10)"
        state = filled_except(field_id)
        state["user_input"] = user_input
        state["completion_percentage"] = 50
        state["history"] = [{
            "role": "agent",
            "message": "On a scale of 0 to 10, how severe is it right now?",
            "question_field_ids": [field_id],
        }]
        result = run(state, FakeModel(mappings={user_input: mapping()}))
        return field_id, state, result

    def test_goodbye_is_flagged_and_the_required_question_is_asked_again(self):
        field_id, state, result = self._asked_severity("Goodbye")

        self.assertTrue(result["end_requested"])
        step = next_intake_step({**state, **result}, result["history"])
        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertEqual(step["question_field_ids"], [field_id])
        self.assertEqual(step["phase"], "interviewing")

    def test_an_ordinary_reply_is_not_an_end_request(self):
        _, _, result = self._asked_severity("I'm done with running because of the pain")

        self.assertFalse(result["end_requested"])


class EndRequestIsNotASkipTests(unittest.TestCase):
    """Saying "end my interview" while questions are open must not open the skip prompt."""

    ASKED = [
        "Pain Assessment.Primary Location of Pain",
        "Pain Assessment.Severity (1-10)",
        "Pain Assessment.Aggravating Factors",
        "Pain Assessment.Relieving Factors",
    ]

    def _end(self, text):
        state = filled_except(*self.ASKED)
        state["user_input"] = text
        state["completion_percentage"] = 50
        state["history"] = [{"role": "agent", "message": "Could you also tell me:",
                             "question_field_ids": self.ASKED}]
        # The model reads "end my interview" as a refusal of the whole batch.
        model = FakeModel(mappings={text: {**mapping(), "decline_scope": "unspecified"}})
        return state, run(state, model)

    def test_it_does_not_ask_which_question_to_skip(self):
        for text in ("end my interview", "i want to end interview", "i want to end interviw"):
            with self.subTest(text=text):
                _, result = self._end(text)

                self.assertTrue(result["end_requested"])
                self.assertNotIn("Which of these would you like to skip?", result.get("response_text", ""))
                self.assertFalse(result.get("direct_response_handled", False))

    def test_it_is_not_counted_as_refusing_a_mandatory_question(self):
        _, result = self._end("end my interview")

        self.assertFalse(result["mandatory_notice"])
        self.assertFalse(result["decline_notice"])

    def test_the_patient_is_told_the_required_questions_come_first(self):
        state, result = self._end("end my interview")

        step = next_intake_step({**state, **result}, result["history"])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertEqual(
            set(step["question_field_ids"]), set(self.ASKED) - {"Pain Assessment.Relieving Factors"}
        )


class EndConfirmationReplyTests(unittest.TestCase):
    """The reply to "are you sure you want to end the interview?"."""

    OPEN = "Treatment Goals.Short-Term Goals (within 3 months)"

    def _state(self, user_input):
        state = filled_except(self.OPEN)
        state["user_input"] = user_input
        state["completion_percentage"] = 50
        state["history"] = [
            {"role": "agent", "message": END_CONFIRM, "end_confirm": True},
        ]
        return state

    def test_yes_is_read_without_mapping_the_reply_onto_any_field(self):
        state = self._state("yes")
        model = FakeModel()

        result = run(state, model)

        self.assertEqual(model.prompts, [])
        self.assertTrue(result["end_requested"])
        self.assertEqual(result["end_reply"], "confirmed")

    def test_no_is_read_as_carrying_on(self):
        for text in ("no", "no I want to continue", "keep going"):
            with self.subTest(text=text):
                model = FakeModel()

                result = run(self._state(text), model)

                self.assertEqual(model.prompts, [])
                self.assertFalse(result["end_requested"])
                self.assertEqual(result["end_reply"], "declined")

    def test_an_unrelated_reply_is_treated_as_an_ordinary_answer(self):
        reply = "I would like to run a marathon again"
        state = self._state(reply)
        model = FakeModel(mappings={reply: mapping(answers=[answer(self.OPEN, "Run a marathon")])})

        result = run(state, model)

        self.assertTrue(model.prompts)
        self.assertEqual(result["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"], "Run a marathon")
        self.assertNotIn("end_reply", result)

    def test_yes_then_ends_with_the_summary_and_no_carries_on(self):
        state = self._state("yes")
        result = run(state, FakeModel())
        ended = next_intake_step({**state, **result}, result["history"])
        self.assertEqual(ended["phase"], "summary")

        state = self._state("no")
        result = run(state, FakeModel())
        carried_on = next_intake_step({**state, **result}, result["history"])
        self.assertTrue(carried_on["response_text"].startswith(END_CONTINUE))
        self.assertEqual(carried_on["question_field_ids"], [self.OPEN])


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
        self.assertTrue(any(
            text.format(labels="Knee pain") in step["response_text"] for text in VARIANTS["noted"]
        ))
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


class RestatedComplaintTests(unittest.TestCase):
    """A body area or a reworded complaint is not a new problem."""

    def _state(self, user_input, *, additional=None, section="Pain Assessment"):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            visit_context="specific_complaint",
            current_section=section,
            user_input=user_input,
            history=[{
                "role": "agent",
                "message": "Where exactly do you feel the pain or problem?",
                "question_field_ids": ["Pain Assessment.Primary Location of Pain"],
            }],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Back pain"
        if additional:
            state["form"]["Additional Complaint 1"] = {"Primary Complaint": additional}
            state["form_sections"] = list(state["form"])
        return state

    def test_a_location_answer_is_not_noted_as_a_new_complaint_and_keeps_the_answers(self):
        reply = "I think the pain is in my lower back. It is on a scale of eight. Running makes it worse."
        state = self._state(reply)
        # The model also listed "Lower back pain" as a complaint, as it did for the patient.
        model = FakeModel(mappings={reply: mapping(
            answers=[
                answer("Pain Assessment.Primary Location of Pain", "Lower back"),
                answer("Pain Assessment.Severity (1-10)", "8"),
                answer("Pain Assessment.Aggravating Factors", "Running"),
            ],
            complaints=["Lower back pain"],
        )})

        result = run(state, model)

        self.assertEqual(result["noted_complaints"], [])
        self.assertNotIn("Additional Complaint 1", result["form"])
        pain = result["form"]["Pain Assessment"]
        self.assertEqual(pain["Primary Location of Pain"], "Lower back")
        self.assertEqual(pain["Severity (1-10)"], "8/10")
        self.assertEqual(pain["Aggravating Factors"], "Running")

    def test_a_genuinely_different_problem_is_still_noted(self):
        reply = "It's my lower back, and my knee hurts too"
        state = self._state(reply)
        model = FakeModel(mappings={reply: mapping(
            answers=[answer("Pain Assessment.Primary Location of Pain", "Lower back")],
            complaints=["Knee pain"],
        )})

        result = run(state, model)

        self.assertEqual(result["noted_complaints"], ["Knee pain"])
        self.assertEqual(result["form"]["Additional Complaint 1"]["Primary Complaint"], "Knee pain")

    def test_saying_it_is_the_same_issue_removes_the_extra_complaint_from_any_section(self):
        reply = "Lower back pain is not a different issue. It is the same issue."
        state = self._state(reply, additional="Lower back pain")

        result = run(state, FakeModel(mappings={reply: mapping()}))

        self.assertNotIn("Additional Complaint 1", result["form"])
        self.assertNotIn("Additional Complaint 1", result["form_sections"])
        self.assertTrue(result["response_text"].startswith("Understood, that's the same issue"))
        self.assertTrue(result["direct_response_handled"])

    def test_saying_it_is_the_same_does_not_remove_a_genuine_second_complaint(self):
        reply = "It is the same issue"
        state = self._state(reply, additional="Knee pain")

        result = run(state, FakeModel(mappings={reply: mapping()}))

        self.assertNotIn("direct_response_handled", result)
        self.assertEqual(result["form"]["Additional Complaint 1"]["Primary Complaint"], "Knee pain")


class LocationAnswerTests(unittest.TestCase):
    """The body area is a real location even when the complaint is named after it."""

    LOCATION = "Pain Assessment.Primary Location of Pain"

    def _state(self, user_input, asked):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            visit_context="specific_complaint",
            current_section="Pain Assessment",
            user_input=user_input,
            history=[{"role": "agent", "message": "Where exactly do you feel the pain?",
                      "question_field_ids": asked}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Knee pain"
        return state

    def test_my_knee_is_accepted_as_the_location_of_knee_pain_when_it_was_asked(self):
        for reply in ("It's in my knee.", "Knee knee pain knee", "I feel the pain in my knee"):
            with self.subTest(reply=reply):
                state = self._state(reply, [self.LOCATION])
                model = FakeModel(mappings={reply: mapping(answers=[answer(self.LOCATION, "Knee")])})

                result = run(state, model)

                self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "Knee")

    def test_the_location_is_kept_along_with_the_other_answers_in_the_same_reply(self):
        reply = "In the knee it is around six and nothing gives it relief"
        asked = [self.LOCATION, "Pain Assessment.Severity (1-10)", "Pain Assessment.Relieving Factors"]
        state = self._state(reply, asked)
        model = FakeModel(mappings={reply: mapping(answers=[
            answer(self.LOCATION, "Knee"),
            answer("Pain Assessment.Severity (1-10)", "6"),
            answer("Pain Assessment.Relieving Factors", "Nothing"),
        ])})

        pain = run(state, model)["form"]["Pain Assessment"]

        self.assertEqual(pain["Primary Location of Pain"], "Knee")
        self.assertEqual(pain["Severity (1-10)"], "6/10")
        self.assertEqual(pain["Relieving Factors"], "Nothing")

    def test_a_location_the_patient_never_mentioned_is_not_invented(self):
        reply = "It started a year ago"
        state = self._state(reply, ["Present Complaint.Duration of the Issue"])
        model = FakeModel(mappings={reply: mapping(answers=[
            answer("Present Complaint.Duration of the Issue", "1 year"),
            answer(self.LOCATION, "Knee"),
        ])})

        result = run(state, model)

        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "")
        self.assertEqual(result["form"]["Present Complaint"]["Duration of the Issue"], "1 year")

    def test_a_location_named_in_the_complaint_is_kept_when_the_patient_mentions_it(self):
        reply = "I have knee pain and it started a year ago"
        state = self._state(reply, ["Present Complaint.Duration of the Issue"])
        model = FakeModel(mappings={reply: mapping(answers=[
            answer("Present Complaint.Duration of the Issue", "1 year"),
            answer(self.LOCATION, "Knee"),
        ])})

        result = run(state, model)

        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "Knee")

    def test_a_more_specific_location_replaces_a_general_one(self):
        reply = "It is in my lower back"
        state = self._state(reply, [])
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Back"
        model = FakeModel(mappings={reply: mapping(answers=[answer(self.LOCATION, "Lower back")])})

        result = run(state, model)

        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "Lower back")

    def test_a_less_specific_location_does_not_replace_a_detailed_one(self):
        reply = "My back hurts"
        state = self._state(reply, [])
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Lower back"
        model = FakeModel(mappings={reply: mapping(answers=[answer(self.LOCATION, "Back")])})

        result = run(state, model)

        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "Lower back")


class RevisionAndLocationFlowTests(unittest.TestCase):
    """Location from the patient's words, and acknowledging an answer the patient changed."""

    LOCATION = "Pain Assessment.Primary Location of Pain"
    SEVERITY = "Pain Assessment.Severity (1-10)"

    def test_traps_pain_maps_traps_as_the_location_when_the_patient_mentions_it(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing", visit_context="specific_complaint", current_section="Pain Assessment",
            user_input="it is in my traps", history=[{"role": "agent", "message": "Where is the pain?",
                                                       "question_field_ids": [self.LOCATION]}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Traps pain"
        model = FakeModel(mappings={state["user_input"]: mapping(answers=[answer(self.LOCATION, "Traps")])})

        result = run(state, model)

        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], "Traps")

    def test_an_updated_answer_is_acknowledged_before_the_open_questions(self):
        state = filled_except("Pain Assessment.Relieving Factors")
        state["form"]["Pain Assessment"]["Severity (1-10)"] = "8/10"
        state["user_input"] = "actually the pain is 7 out of 10"
        state["history"] = [{"role": "agent", "message": "Anything that gives relief?",
                             "question_field_ids": ["Pain Assessment.Relieving Factors"]}]
        correction = {**answer(self.SEVERITY, "7/10"), "correction": True}
        model = FakeModel(mappings={state["user_input"]: mapping(answers=[correction])})

        result = run(state, model)
        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "7/10")
        self.assertEqual(result["revised_answers"], [{"field": "Severity (1-10)", "value": "7/10"}])

        step = next_intake_step({**state, **result}, result["history"])
        self.assertTrue(any(
            step["response_text"].startswith(
                revision.format(changes="Severity to 7/10") + " " + return_line
            )
            for revision in VARIANTS["revision"]
            for return_line in VARIANTS["return_to_questions"]
        ))
        self.assertNotIn("Sorry", step["response_text"])
        self.assertEqual(step["question_field_ids"], ["Pain Assessment.Relieving Factors"])

    def test_new_answers_are_listed_as_saved_and_updates_are_not(self):
        state = filled_except("Pain Assessment.Relieving Factors", "Pain Assessment.Aggravating Factors")
        state["form"]["Pain Assessment"]["Severity (1-10)"] = "8/10"
        state["user_input"] = "nothing helps, it gets worse when I run, and actually it is 7"
        state["history"] = [{"role": "agent", "message": "Anything that helps?",
                             "question_field_ids": ["Pain Assessment.Relieving Factors",
                                                    "Pain Assessment.Aggravating Factors"]}]
        model = FakeModel(mappings={state["user_input"]: mapping(answers=[
            answer("Pain Assessment.Relieving Factors", "Nothing gives relief"),
            answer("Pain Assessment.Aggravating Factors", "Running"),
            {**answer(self.SEVERITY, "7/10"), "correction": True},
        ])})

        result = run(state, model)

        self.assertEqual(set(result["saved_answers"]), {"Relieving Factors", "Aggravating Factors"})
        self.assertEqual([item["field"] for item in result["revised_answers"]], ["Severity (1-10)"])

    def test_a_first_answer_is_not_reported_as_an_update(self):
        state = filled_except("Pain Assessment.Severity (1-10)")
        state["user_input"] = "it is 6 out of 10"
        state["history"] = [{"role": "agent", "message": "How severe?",
                             "question_field_ids": ["Pain Assessment.Severity (1-10)"]}]
        model = FakeModel(mappings={state["user_input"]: mapping(answers=[answer(self.SEVERITY, "6")])})

        result = run(state, model)

        self.assertEqual(result["revised_answers"], [])


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
