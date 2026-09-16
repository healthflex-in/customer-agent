import unittest
from unittest.mock import patch
import sys
import types

from src.graph.pure_functions.form_extraction import detect_form_correction
from src.graph.pure_functions.referral import (
    is_referral_question,
    normalize_referral_source,
)
from src.graph.pure_functions.summary import classify_summary_response
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.extract import make_extract_node
    from src.graph.nodes.generate import make_generate_question_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    # These node modules need only get_stream_writer for the unit exercised
    # here. Provide a test-local stand-in when the lightweight developer
    # environment has not installed the full LangGraph runtime.
    langgraph_module = types.ModuleType("langgraph")
    langgraph_config_module = types.ModuleType("langgraph.config")
    langgraph_config_module.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", langgraph_module)
    sys.modules["langgraph.config"] = langgraph_config_module
    from src.graph.nodes.extract import make_extract_node
    from src.graph.nodes.generate import make_generate_question_node


class ReferralFlowRegressionTests(unittest.TestCase):
    def test_recognizes_referral_question_and_short_answer(self):
        self.assertTrue(is_referral_question("How did you come to know about us?"))
        self.assertTrue(is_referral_question("How were you referred to us?"))
        self.assertEqual(normalize_referral_source("I heard from a friend"), "Friend/word of mouth")

    def test_summary_referral_answer_is_a_concrete_correction(self):
        form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
        form["Referral"]["Source"] = "Google"

        def unexpected_llm(_prompt):
            self.fail("Known referral answers must not need an LLM correction call")

        intent = classify_summary_response("from friend", "summary", unexpected_llm)
        correction = detect_form_correction(
            "from friend", form, is_summary_mode=True, llm_complete=unexpected_llm
        )

        self.assertEqual(intent["intent"], "request_change")
        self.assertEqual(intent["correction_text"], "from friend")
        self.assertEqual(correction["section_name"], "Referral")
        self.assertEqual(correction["field_name"], "Source")
        self.assertEqual(correction["new_value"], "Friend/word of mouth")

    def test_referral_answer_is_captured_when_question_came_from_llm(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        state["history"] = [
            {"role": "agent", "message": "How did you hear about Stance Health?"}
        ]
        state["user_input"] = "from friend"

        def reasoning_llm(_prompt):
            import json
            return json.dumps(state["form"])

        def llm_complete(prompt):
            if "Determine whether the patient" in prompt or "diagnostic reports" in prompt:
                return '{"has_reports": null, "wants_upload": null}'
            return "THINKING: referral was answered\nQUESTION: DONE"

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(llm_complete, reasoning_llm=reasoning_llm)(state)

        self.assertTrue(result["referral_asked"])
        self.assertEqual(
            result["form"]["Referral"]["Source"], "Friend/word of mouth"
        )

    def test_prefetched_referral_question_is_not_repeated(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        state["referral_asked"] = True
        state["form"]["Referral"]["Source"] = "Friend/word of mouth"
        state["pending_question"] = "How did you come to know about us?"
        state["history"] = [{"role": "user", "message": "from friend"}]

        with patch("src.graph.nodes.generate.get_stream_writer", return_value=lambda _event: None):
            result = make_generate_question_node(
                lambda _prompt: "unused", "unused"
            )(state)

        self.assertFalse(is_referral_question(result["response_text"]))


if __name__ == "__main__":
    unittest.main()
