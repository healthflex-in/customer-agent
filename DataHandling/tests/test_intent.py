"""The model says what the patient meant; end and skip are added to the existing detection."""

import json
import sys
import types
import unittest
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.pure_functions.answer_mapping import map_reply, read_open_message
from src.graph.pure_functions.clarification import OUT_OF_FLOW_RESPONSE
from src.graph.pure_functions.phrasing import VARIANTS, normalise_intent
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


QUESTION = "About your piriformis tightness: How long have you been experiencing this issue?"
DURATION = "Present Complaint.Duration of the Issue"


def mapping_json(intent="answer", answers=(), scope="specified"):
    return json.dumps({
        "visit_context": "specific_complaint", "answers": list(answers),
        "reports": {"has_reports": None, "wants_upload": None}, "complaints": [],
        "decline_scope": scope, "tone": "neutral", "intent": intent,
    })


class FakeLLM:
    """Answers the open-message check and the mapping prompt, and records every prompt."""

    def __init__(self, *, kind="other", mapping=None):
        self.kind = kind
        self.mapping = mapping or mapping_json()
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if "What is the reply?" in prompt:
            return json.dumps({"kind": self.kind})
        return self.mapping

    @property
    def form_question_calls(self):
        return [p for p in self.prompts if "What is the reply?" in p]


def two_complaint_state(user_input, asked=(DURATION,), question=QUESTION):
    state = get_fresh_interview_state("u", "FRM-01", "s")
    form = state["form"]
    form["Present Complaint"]["Primary Complaint"] = "Piriformis tightness"
    form["Additional Complaint 1"] = {
        "Primary Complaint": "Left knee pain", "Duration of the Issue": "", "Onset (Gradual or Sudden)": "",
    }
    state.update(
        phase="interviewing", visit_context="specific_complaint", form=form, form_sections=list(form),
        current_section="Present Complaint", user_input=user_input,
        history=[{
            "role": "agent", "message": f"Thanks, I've noted that.\n\n{question}",
            "question_field_ids": list(asked), "question_text": question,
        }],
    )
    return state


def run(state, llm):
    with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
        return make_extract_node(llm, reasoning_llm=llm)(state)


class IntentFieldTests(unittest.TestCase):
    def _intent(self, payload):
        form = load_form("FRM-01").empty_form()
        return map_reply(
            reply="hello", last_question="", form=form, form_sections=list(form),
            visit_context="specific_complaint", llm=lambda _prompt: payload,
        ).intent

    def test_the_mapping_reports_the_intent(self):
        for intent in ("answer", "skip", "end", "form_question", "medical_advice", "off_topic"):
            with self.subTest(intent=intent):
                self.assertEqual(self._intent(mapping_json(intent=intent)), intent)

    def test_a_missing_or_unknown_intent_is_answer(self):
        self.assertEqual(self._intent(json.dumps({"answers": []})), "answer")
        self.assertEqual(self._intent(mapping_json(intent="chitchat")), "answer")
        self.assertEqual(normalise_intent(None), "answer")

    def test_the_prompt_defines_every_intent(self):
        prompts = []
        form = load_form("FRM-01").empty_form()
        map_reply(
            reply="hi", last_question="", form=form, form_sections=list(form),
            visit_context="unknown", llm=lambda prompt: prompts.append(prompt) or "{}",
        )

        for word in ('"skip"', '"end"', '"form_question"', '"medical_advice"', '"off_topic"'):
            self.assertIn(word, prompts[0])
        self.assertIn('"intent": "answer"', prompts[0])


class OpenMessageReadingTests(unittest.TestCase):
    def _reading(self, kind):
        return read_open_message("which issue?", QUESTION, ["a", "b"], lambda _p: json.dumps({"kind": kind}))

    def test_it_follows_the_models_reading(self):
        for kind in ("answer", "form_question", "other"):
            with self.subTest(kind=kind):
                self.assertEqual(self._reading(kind), kind)

    def test_a_failure_or_a_bad_reading_is_other_so_the_boundary_stays(self):
        def boom(_prompt):
            raise RuntimeError("down")

        self.assertEqual(read_open_message("x", QUESTION, [], boom), "other")
        self.assertEqual(read_open_message("x", QUESTION, [], lambda _p: "not json"), "other")
        self.assertEqual(self._reading("chitchat"), "other")
        self.assertEqual(read_open_message("x", QUESTION, [], lambda _p: json.dumps({"kind": ["answer"]})), "other")

    def test_the_prompt_lists_the_patients_problems_and_says_hedged_answers_are_answers(self):
        seen = []
        read_open_message("which issue?", QUESTION, ["Piriformis tightness", "Left knee pain"],
                          lambda prompt: seen.append(prompt) or "{}")

        self.assertIn("Piriformis tightness; Left knee pain", seen[0])
        self.assertIn('"may be a few days"', seen[0])


class WhichIssueTests(unittest.TestCase):
    """The reported case: with two complaints the patient asks which one a question is about."""

    def test_the_patient_is_told_which_problem_and_the_question_is_repeated(self):
        llm = FakeLLM(kind="form_question")

        result = run(two_complaint_state("which issue the knee pain or the tightness"), llm)

        self.assertTrue(result["direct_response_handled"])
        self.assertTrue(any(
            result["response_text"].startswith(line.format(complaint="your piriformis tightness"))
            for line in VARIANTS["form_question"]
        ))
        self.assertTrue(result["response_text"].endswith(QUESTION))
        self.assertNotIn(OUT_OF_FLOW_RESPONSE, result["response_text"])
        self.assertNotIn("Thanks, I've noted", result["response_text"])

    def test_the_pending_questions_survive_the_reply(self):
        result = run(two_complaint_state("which issue the knee pain or the tightness"), FakeLLM(kind="form_question"))

        last = result["history"][-1]
        self.assertEqual(last["question_field_ids"], [DURATION])
        self.assertEqual(last["question_text"], QUESTION)

    def test_the_question_is_not_counted_as_asked_again(self):
        state = two_complaint_state("which issue?")
        state["question_ask_counts"] = {DURATION: 1}

        result = run(state, FakeLLM(kind="form_question"))

        self.assertNotIn("question_ask_counts", result)

    def test_when_it_is_not_about_the_question_the_boundary_reply_quotes_only_the_question(self):
        result = run(two_complaint_state("which issue?"), FakeLLM(kind="other"))

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))
        self.assertTrue(result["response_text"].endswith(QUESTION))
        self.assertNotIn("Thanks, I've noted", result["response_text"])

    def test_with_no_question_pending_a_form_question_reading_keeps_the_boundary_reply(self):
        state = two_complaint_state("which issue?")
        state["history"] = [{"role": "agent", "message": "Welcome"}]
        llm = FakeLLM(kind="form_question")

        result = run(state, llm)

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))


ORDINARY_ANSWERS = (
    # Each of these was refused as an "unrelated question" by the original code.
    "may be from few days", "may be few days", "could be two weeks", "can't remember exactly",
    "do not remember", "does not matter, about a week", "will say about 3 days",
    "where my hip meets my leg", "which one, the left side", "who knows, the whole thigh",
    "what makes it worse is sitting", "when I run", "when I climb stairs", "how I sit seems to matter",
    "how it started was gradual", "is hard to say, maybe gradual", "am not sure, gradual I think",
)


class OrdinaryAnswersTests(unittest.TestCase):
    """An answer that merely starts like a question is processed as an answer."""

    def _duration_answer(self):
        return mapping_json(answers=[{
            "field_id": DURATION, "status": "answered", "value": "A few days", "detail": "", "correction": False,
        }])

    def test_the_reported_case_may_be_from_few_days_is_mapped(self):
        state = two_complaint_state("may be from feww days")
        state["history"] = [{
            "role": "agent",
            "message": "Welcome back. Your previous answers are saved.\n\nLet's continue.\n\n" + QUESTION,
            "question_field_ids": [DURATION], "question_text": QUESTION,
        }]
        llm = FakeLLM(kind="answer", mapping=self._duration_answer())

        result = run(state, llm)

        self.assertFalse(result.get("direct_response_handled", False))
        self.assertNotIn(OUT_OF_FLOW_RESPONSE, result.get("response_text", ""))
        self.assertEqual(result["form"]["Present Complaint"]["Duration of the Issue"], "A few days")

    def test_every_ordinary_answer_gets_through_when_the_model_reads_it_as_an_answer(self):
        for text in ORDINARY_ANSWERS:
            with self.subTest(text=text):
                result = run(two_complaint_state(text), FakeLLM(kind="answer", mapping=self._duration_answer()))

                self.assertFalse(result.get("direct_response_handled", False))
                self.assertNotIn(OUT_OF_FLOW_RESPONSE, result.get("response_text", ""))

    def test_the_boundary_is_kept_if_the_model_cannot_tell_so_advice_never_slips_through(self):
        for text in ORDINARY_ANSWERS[:3]:
            with self.subTest(text=text):
                result = run(two_complaint_state(text), FakeLLM(kind="other"))

                self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))

    def test_the_model_is_only_asked_about_messages_the_keyword_check_refused(self):
        llm = FakeLLM(kind="answer")

        run(two_complaint_state("it has been about two weeks"), llm)
        run(two_complaint_state("what exercise should I do?"), llm)

        self.assertEqual(llm.form_question_calls, [])


class MixedMessageStaysAsBeforeTests(unittest.TestCase):
    def test_when_the_model_cannot_tell_a_message_with_facts_keeps_the_old_mixed_reply(self):
        """"how it started was gradual" holds a fact; without an "answer" reading the old behaviour stands."""

        result = run(two_complaint_state("how it started was gradual"), FakeLLM(kind="other"))

        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))

    def test_when_the_model_reads_it_as_an_answer_the_fact_is_kept_and_no_refusal_is_shown(self):
        onset = {"field_id": "Present Complaint.Onset (Gradual or Sudden)", "status": "answered",
                 "value": "Gradual", "detail": "", "correction": False}

        result = run(
            two_complaint_state("how it started was gradual"),
            FakeLLM(kind="answer", mapping=mapping_json(answers=[onset])),
        )

        self.assertNotIn(OUT_OF_FLOW_RESPONSE, result.get("response_text", ""))
        self.assertEqual(result["form"]["Present Complaint"]["Onset (Gradual or Sudden)"], "Gradual")


class HardBoundaryStaysFixedTests(unittest.TestCase):
    def test_medical_advice_never_consults_the_model_and_keeps_the_boundary(self):
        for text in ("what exercise should I do for this?", "should I take ibuprofen?", "what could it be?"):
            with self.subTest(text=text):
                llm = FakeLLM(kind="form_question")

                result = run(two_complaint_state(text), llm)

                self.assertEqual(llm.form_question_calls, [])
                self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))

    def test_a_clearance_question_the_keywords_miss_is_still_refused_when_the_model_reads_it_as_other(self):
        llm = FakeLLM(kind="other")

        result = run(two_complaint_state("can I go running tomorrow?"), llm)

        self.assertEqual(len(llm.form_question_calls), 1)
        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))

    def test_an_ordinary_answer_never_consults_the_model(self):
        llm = FakeLLM(kind="form_question")

        run(two_complaint_state("it has been about two weeks"), llm)

        self.assertEqual(llm.form_question_calls, [])


class EndAndSkipIntentTests(unittest.TestCase):
    def test_the_models_end_intent_is_added_to_the_phrase_check(self):
        llm = FakeLLM(mapping=mapping_json(intent="end"))

        result = run(two_complaint_state("I think I am out of here, enough"), llm)

        self.assertTrue(result["end_requested"])

    def test_an_end_intent_is_ignored_when_the_reply_also_answered_something(self):
        answer = {"field_id": DURATION, "status": "answered", "value": "2 weeks", "detail": "", "correction": False}
        llm = FakeLLM(mapping=mapping_json(intent="end", answers=[answer]))

        result = run(two_complaint_state("two weeks, and I am done"), llm)

        self.assertFalse(result["end_requested"])

    def test_the_existing_phrase_check_still_ends_without_the_models_help(self):
        result = run(two_complaint_state("goodbye"), FakeLLM(mapping=mapping_json(intent="answer")))

        self.assertTrue(result["end_requested"])

    def test_the_models_skip_intent_opens_the_which_one_prompt_for_several_optional_questions(self):
        asked = ["Pain Assessment.Relieving Factors", "History & Diagnostics.Reports"]
        llm = FakeLLM(mapping=mapping_json(intent="skip", scope="specified"))

        result = run(two_complaint_state("next one please", asked=asked, question="Could you also tell me:"), llm)

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("Which of these would you like to skip?", result["response_text"])

    def test_a_skip_intent_with_an_answer_is_not_a_skip(self):
        asked = ["Pain Assessment.Relieving Factors", "History & Diagnostics.Reports"]
        answer = {"field_id": "Pain Assessment.Relieving Factors", "status": "answered",
                  "value": "Ice", "detail": "", "correction": False}
        llm = FakeLLM(mapping=mapping_json(intent="skip", answers=[answer]))

        result = run(two_complaint_state("ice helps, skip the rest", asked=asked, question="Could you also tell me:"), llm)

        self.assertFalse(result.get("direct_response_handled", False))


if __name__ == "__main__":
    unittest.main()
