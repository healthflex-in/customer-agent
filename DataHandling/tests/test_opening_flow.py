"""The opening question, a confused first reply, and the one-issue line in the focus question."""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.nodes.first_turn import make_handle_first_turn_node
from src.graph.pure_functions.clarification import OUT_OF_FLOW_RESPONSE, out_of_flow_kind
from src.graph.pure_functions.resume import build_resume_plan
from src.graph.state import get_fresh_interview_state
from src.prompts import INITIAL_INTAKE_PROMPT, OPENING_GREETING, OPENING_PURPOSE, OPENING_QUESTION

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


CONFUSED = "I don't know what I'm doing here. What is the information you want from me again? I just booked the assessment online."
INTERVIEW_HANDLER = Path(__file__).resolve().parents[1] / "app" / "ws" / "handlers" / "interview.py"


class OpeningMessageTests(unittest.TestCase):
    def test_the_full_opening_text_is_unchanged(self):
        self.assertEqual(
            INITIAL_INTAKE_PROMPT,
            "Hey, I'm glad you're here! This is your clinical intake form. Your answers help your "
            "clinician prepare for your visit. What is the main problem bothering you today?",
        )

    def test_it_is_built_from_a_greeting_a_purpose_and_a_question(self):
        self.assertEqual(INITIAL_INTAKE_PROMPT, f"{OPENING_GREETING} {OPENING_PURPOSE} {OPENING_QUESTION}")
        self.assertEqual(OPENING_QUESTION, "What is the main problem bothering you today?")

    def test_every_opening_history_entry_stores_the_question_on_its_own(self):
        fresh = get_fresh_interview_state("u", "FRM-01", "s")["history"][0]
        first_turn = make_handle_first_turn_node(INITIAL_INTAKE_PROMPT, OPENING_QUESTION)(
            {"user_input": "yes", "history": []}
        )["history"][0]
        form = load_form("FRM-01").empty_form()
        resumed = build_resume_plan(form)
        resume_entry = resumed.agent_entry(resumed.message)

        for entry in (fresh, first_turn, resume_entry):
            with self.subTest(entry=entry["message"][:20]):
                self.assertEqual(entry["message"], INITIAL_INTAKE_PROMPT)
                self.assertEqual(entry["question_text"], OPENING_QUESTION)

    def test_the_live_start_path_stores_it_too(self):
        source = INTERVIEW_HANDLER.read_text(encoding="utf-8")

        self.assertIn('"message": _welcome_text, "question_text": OPENING_QUESTION', source)


class WhatIsIsReadByTheModelTests(unittest.TestCase):
    def test_what_is_and_explain_are_open_questions_not_hard_refusals(self):
        q = INITIAL_INTAKE_PROMPT
        for text in (CONFUSED, "what is a slipped disc?", "explain sciatica", "tell me about herniated discs",
                     "what are the types of back pain?", "how does physio work?"):
            with self.subTest(text=text):
                self.assertEqual(out_of_flow_kind(text, q), "open")

    def test_advice_medication_and_the_clinic_stay_hard(self):
        q = INITIAL_INTAKE_PROMPT
        for text in ("what exercise should I do?", "should I take ibuprofen?", "what could it be?",
                     "what causes this?", "tell me about stance health", "what are the symptoms of a slipped disc?"):
            with self.subTest(text=text):
                self.assertEqual(out_of_flow_kind(text, q), "hard")


class FakeLLM:
    def __init__(self, kind="other", fail=False):
        self.kind, self.fail, self.prompts = kind, fail, []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "What is the reply?" in prompt:
            if self.fail:
                raise RuntimeError("model down")
            return json.dumps({"kind": self.kind})
        return json.dumps({"visit_context": "unknown", "answers": [], "complaints": [],
                           "reports": {"has_reports": None, "wants_upload": None},
                           "tone": "neutral", "intent": "answer"})


def first_reply_state(user_input):
    state = get_fresh_interview_state("u", "FRM-01", "s")
    state.update(phase="interviewing", user_input=user_input)
    return state


def run(state, llm):
    with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _e: None):
        return make_extract_node(llm, reasoning_llm=llm)(state)


class ConfusedFirstReplyTests(unittest.TestCase):
    def test_a_confused_patient_gets_the_purpose_and_the_question_only(self):
        result = run(first_reply_state(CONFUSED), FakeLLM(kind="form_question"))

        self.assertTrue(result["direct_response_handled"])
        self.assertEqual(result["response_text"], f"{OPENING_PURPOSE}\n\n{OPENING_QUESTION}")
        self.assertNotIn("unable to answer", result["response_text"])
        self.assertNotIn(OPENING_GREETING, result["response_text"])

    def test_the_reply_keeps_the_question_for_the_next_turn_and_counts_no_ask(self):
        result = run(first_reply_state(CONFUSED), FakeLLM(kind="form_question"))

        self.assertEqual(result["history"][-1]["question_text"], OPENING_QUESTION)
        self.assertNotIn("question_ask_counts", result)

    def test_an_education_question_gets_the_refusal_and_only_the_question(self):
        result = run(first_reply_state("what is a slipped disc?"), FakeLLM(kind="other"))

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))
        self.assertTrue(result["response_text"].endswith(OPENING_QUESTION))
        self.assertNotIn(OPENING_GREETING, result["response_text"])

    def test_if_the_model_fails_the_boundary_reply_is_given(self):
        result = run(first_reply_state(CONFUSED), FakeLLM(fail=True))

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))

    def test_medical_advice_never_reaches_the_model(self):
        llm = FakeLLM(kind="answer")

        result = run(first_reply_state("what could it be?"), llm)

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))
        self.assertEqual([p for p in llm.prompts if "What is the reply?" in p], [])


class FocusQuestionTests(unittest.TestCase):
    def _focus(self):
        from src.graph.nodes.extract import _ask_focus

        state = get_fresh_interview_state("u", "FRM-01", "s")
        return _ask_focus(state, state["form"], [], ["Neck pain", "Lower back pain", "Stiffness"])["response_text"]

    def test_it_says_the_assessment_focuses_on_one_issue_but_others_can_be_shared(self):
        text = self._focus()

        self.assertEqual(text.count("For this assessment we'll focus on one issue"), 1)
        self.assertIn("but you can still share details about the others.", text)

    def test_the_rest_of_the_message_and_its_order_are_unchanged(self):
        text = self._focus()

        self.assertTrue(text.startswith("You mentioned more than one concern:\n- Neck pain\n- Lower back pain\n- Stiffness"))
        self.assertTrue(text.endswith("Which one would you like to focus on first? We'll go through the others after that."))
        self.assertLess(text.index("For this assessment"), text.index("Which one would you like to focus on first?"))


if __name__ == "__main__":
    unittest.main()
