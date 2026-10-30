"""A bare "no" or "yes" answers the question it fits. It is never a refusal, and never a skip."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.pure_functions.answer_mapping import map_reply
from src.graph.pure_functions.intent_detection import bare_yes_no
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


REPORTS = "History & Diagnostics.Reports"
HEALTH = "History & Diagnostics.Systemic Illness and Surgical History"
WORK = "History & Diagnostics.Current Lifestyle — Work"
ACTIVITY = "History & Diagnostics.Current Lifestyle — Activity or Exercise"
HISTORY_BATCH = [HEALTH, WORK, ACTIVITY, REPORTS]


def mapped(reply, answers, asked=HISTORY_BATCH):
    form = load_form("FRM-01").empty_form()
    payload = json.dumps({
        "visit_context": "specific_complaint", "answers": answers,
        "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
        "decline_scope": "specified", "tone": "neutral", "intent": "answer",
    })
    return map_reply(reply=reply, last_question="history", form=form, form_sections=list(form),
                     visit_context="specific_complaint", llm=lambda _p: payload, asked_ids=list(asked))


def declined(field_id):
    return {"field_id": field_id, "status": "declined", "value": "", "detail": "", "correction": False}


class BareYesNoTests(unittest.TestCase):
    def test_a_whole_reply_of_yes_or_no_is_recognised(self):
        for text, expected in (("no", "No"), ("No.", "No"), ("nope", "No"), ("none", "No"),
                               ("nothing", "No"), ("yes", "Yes"), ("Yeah", "Yes"), ("yes I do", "Yes")):
            with self.subTest(text=text):
                self.assertEqual(bare_yes_no(text), expected)

    def test_a_yes_or_no_with_detail_is_not_bare(self):
        for text in ("no reports", "no other health issues", "no I don't have reports", "skip", "", None):
            with self.subTest(text=text):
                self.assertIsNone(bare_yes_no(text))


class NoIsAnAnswerTests(unittest.TestCase):
    def test_a_no_the_model_reads_as_a_decline_is_saved_as_an_answer(self):
        result = mapped("no", [declined(HEALTH), declined(REPORTS)])

        self.assertEqual(result.form["History & Diagnostics"]["Reports"], "No")
        self.assertEqual(result.form["History & Diagnostics"]["Systemic Illness and Surgical History"], "No")
        self.assertEqual(result.declined_field_ids, [])
        self.assertFalse(result.decline_unspecified)

    def test_a_no_to_reports_alone_answers_reports(self):
        result = mapped("no", [{"field_id": REPORTS, "status": "answered", "value": "No reports",
                                "detail": "", "correction": False}], asked=[REPORTS])

        self.assertEqual(result.form["History & Diagnostics"]["Reports"], "No reports")

    def test_a_no_that_fits_the_single_question_asked_answers_it(self):
        result = mapped("no", [], asked=[REPORTS])

        self.assertEqual(result.form["History & Diagnostics"]["Reports"], "No")

    def test_an_explicit_skip_is_still_a_decline(self):
        result = mapped("skip", [declined(REPORTS)])

        self.assertEqual(result.declined_field_ids, [REPORTS])
        self.assertEqual(result.form["History & Diagnostics"]["Reports"], "")

    def test_a_longer_reply_with_no_in_it_is_not_changed(self):
        result = mapped("no reports, but I did have an MRI last year", [declined(REPORTS)])

        self.assertEqual(result.declined_field_ids, [REPORTS])


class WhichQuestionTests(unittest.TestCase):
    def _state(self, user_input, asked, counts=None):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing", visit_context="specific_complaint", current_section="History & Diagnostics",
            user_input=user_input, question_ask_counts=counts or {},
            history=[{"role": "agent", "message": "history", "question_field_ids": list(asked)}],
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Back pain"
        state["form_sections"] = list(state["form"])
        return state

    def _run(self, state, payload):
        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _e: None):
            return make_extract_node(lambda _p: payload, reasoning_llm=lambda _p: payload)(state)

    def _empty_payload(self):
        return json.dumps({"visit_context": "specific_complaint", "answers": [],
                           "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
                           "tone": "neutral", "intent": "answer"})

    def test_a_bare_no_that_fits_none_of_several_questions_asks_which_one(self):
        state = self._state("no", HISTORY_BATCH)

        result = self._run(state, self._empty_payload())

        self.assertTrue(result["direct_response_handled"])
        self.assertTrue(result["response_text"].startswith("Which question is that no for?"))
        self.assertIn("- Do you have any related X-rays, MRI, CT scans, or blood reports?", result["response_text"])
        self.assertTrue(result["history"][-1]["yes_no_clarify"])

    def test_the_clarification_saves_nothing_and_counts_no_ask(self):
        state = self._state("no", HISTORY_BATCH, counts={REPORTS: 1})

        result = self._run(state, self._empty_payload())

        self.assertNotIn("question_ask_counts", result)
        self.assertEqual(result["form"]["History & Diagnostics"]["Reports"], "")

    def test_it_is_asked_only_once_per_batch(self):
        state = self._state("no", HISTORY_BATCH)
        state["history"].append({"role": "agent", "message": "which?", "yes_no_clarify": True,
                                 "question_field_ids": HISTORY_BATCH})
        state["user_input"] = "no"

        result = self._run(state, self._empty_payload())

        self.assertFalse(result.get("direct_response_handled", False))

    def test_a_no_that_is_placed_is_not_asked_about_at_all(self):
        state = self._state("no", HISTORY_BATCH)
        payload = json.dumps({"visit_context": "specific_complaint", "answers": [declined(REPORTS)],
                              "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
                              "decline_scope": "specified", "tone": "neutral", "intent": "answer"})

        result = self._run(state, payload)

        self.assertFalse(result.get("direct_response_handled", False))
        self.assertEqual(result["form"]["History & Diagnostics"]["Reports"], "No")


if __name__ == "__main__":
    unittest.main()
