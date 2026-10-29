"""Skip asks which question; only "skip all" exits every remaining question at once."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from src.graph.pure_functions.answer_mapping import decide_skip
from src.graph.pure_functions.intent_detection import is_skip_all
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


from src.forms.loader import load_form

FORM = load_form("FRM-01").empty_form()
SEVERITY = "Pain Assessment.Severity (1-10)"
AGGRAVATING = "Pain Assessment.Aggravating Factors"
RELIEVING = "Pain Assessment.Relieving Factors"
REPORTS = "History & Diagnostics.Reports"
GOALS = "Treatment Goals.Short-Term Goals (within 3 months)"
OPTIONAL_BATCH = [RELIEVING, REPORTS, GOALS]


class SkipAllWordingTests(unittest.TestCase):
    def test_only_explicit_all_words_exit_everything(self):
        for text in ("skip all", "Skip all.", "skip everything", "skip them all", "skip the rest",
                     "none of these", "ok skip all please", "I want to skip all"):
            with self.subTest(text=text):
                self.assertTrue(is_skip_all(text))

    def test_a_bare_skip_or_an_answer_is_not_skip_all(self):
        for text in ("skip", "skip this", "skip reports", "skip the first one", "I don't know",
                     "no reports", "skip this one, the others are fine", "", None):
            with self.subTest(text=text):
                self.assertFalse(is_skip_all(text))


class SkipAllDecisionTests(unittest.TestCase):
    def test_skip_all_declines_every_open_optional_question(self):
        decision = decide_skip(form=FORM, asked_ids=OPTIONAL_BATCH, declined_ids=[], unspecified=True,
                               ask_counts={}, max_asks=3, skip_all=True)

        self.assertEqual(decision.decline_ids, OPTIONAL_BATCH)
        self.assertEqual(decision.ask_which, [])

    def test_skip_all_never_declines_a_mandatory_question(self):
        decision = decide_skip(form=FORM, asked_ids=[SEVERITY, RELIEVING], declined_ids=[], unspecified=True,
                               ask_counts={}, max_asks=3, skip_all=True)

        self.assertEqual(decision.decline_ids, [RELIEVING])
        self.assertTrue(decision.mandatory_refused)

    def test_a_bare_skip_with_several_open_questions_asks_which_one(self):
        decision = decide_skip(form=FORM, asked_ids=OPTIONAL_BATCH, declined_ids=[], unspecified=True,
                               ask_counts={}, max_asks=3)

        self.assertEqual(decision.decline_ids, [])
        self.assertEqual(decision.ask_which, OPTIONAL_BATCH)

    def test_a_bare_skip_with_one_question_left_skips_it(self):
        decision = decide_skip(form=FORM, asked_ids=[RELIEVING], declined_ids=[], unspecified=True,
                               ask_counts={}, max_asks=3)

        self.assertEqual(decision.decline_ids, [RELIEVING])
        self.assertEqual(decision.ask_which, [])


class SkipFlowThroughExtractTests(unittest.TestCase):
    def _state(self, user_input, asked, last_prompt=None):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing", visit_context="specific_complaint", current_section="Pain Assessment",
            user_input=user_input,
            history=[{"role": "agent", "message": last_prompt or "Could you also tell me:",
                      "question_field_ids": list(asked),
                      **({"skip_question_ids": list(asked)} if last_prompt else {})}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Back pain"
        state["form_sections"] = list(state["form"])
        return state

    def _run(self, state, payload):
        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _e: None):
            return make_extract_node(lambda _p: payload, reasoning_llm=lambda _p: payload)(state)

    def _payload(self, scope="unspecified"):
        return json.dumps({"visit_context": "specific_complaint", "answers": [],
                           "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
                           "decline_scope": scope, "tone": "neutral", "intent": "skip"})

    def test_a_bare_skip_asks_which_question(self):
        result = self._run(self._state("skip", OPTIONAL_BATCH), self._payload())

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("Which of these would you like to skip?", result["response_text"])

    def test_skip_all_exits_every_optional_question_without_asking(self):
        result = self._run(self._state("skip all", OPTIONAL_BATCH), self._payload())

        self.assertFalse(result.get("direct_response_handled", False))
        self.assertEqual(result["form"]["Pain Assessment"]["Relieving Factors"], "Skipped by patient")
        self.assertEqual(result["form"]["History & Diagnostics"]["Reports"], "Skipped by patient")

    def test_a_reply_to_which_one_that_is_an_answer_is_not_a_skip(self):
        state = self._state("reports, I have an MRI from last year", [REPORTS], last_prompt="Which of these would you like to skip?")
        payload = json.dumps({"visit_context": "specific_complaint", "answers": [
            {"field_id": REPORTS, "status": "answered", "value": "MRI last year", "detail": "", "correction": False}],
            "reports": {"has_reports": True, "wants_upload": None}, "complaints": [], "tone": "neutral", "intent": "answer"})

        result = self._run(state, payload)

        self.assertEqual(result["form"]["History & Diagnostics"]["Reports"], "MRI last year")

    def test_skip_all_after_the_which_one_prompt_exits_the_pending_questions(self):
        state = self._state("skip all", OPTIONAL_BATCH, last_prompt="Which of these would you like to skip?")

        result = self._run(state, self._payload())

        self.assertEqual(result["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"], "Skipped by patient")


if __name__ == "__main__":
    unittest.main()
