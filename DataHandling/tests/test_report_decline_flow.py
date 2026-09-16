import sys
import types
import unittest
from unittest.mock import patch

from src.graph.nodes.upload import make_handle_upload_response_node
from src.graph.pure_functions.question_repetition import asks_about_answered_field
from src.graph.pure_functions.lifestyle import merge_lifestyle_answer
from src.graph.pure_functions.summary import classify_reports_intent
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.extract import make_extract_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    langgraph_module = types.ModuleType("langgraph")
    langgraph_config_module = types.ModuleType("langgraph.config")
    langgraph_config_module.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", langgraph_module)
    sys.modules["langgraph.config"] = langgraph_config_module
    from src.graph.nodes.extract import make_extract_node


class ReportDeclineFlowTests(unittest.TestCase):
    def test_smoking_alcohol_and_walking_are_registered_without_llm(self):
        value = merge_lifestyle_answer(
            "",
            "Actually I smoke only, I don't drink alcohol. I only walk and "
            "I don't have any other exercise routine.",
        )
        self.assertIn("Smokes", value)
        self.assertIn("Does not drink alcohol", value)
        self.assertIn("Walks for exercise", value)
        self.assertIn("No other regular exercise routine", value)

    def test_report_and_direct_clinician_share_are_deterministic(self):
        def unexpected_llm(_prompt):
            self.fail("Clear report/upload intent must not require an LLM call")

        result = classify_reports_intent(
            "I have the X-rays and I will share them directly with the doctor. "
            "I don't want to upload here.",
            unexpected_llm,
            context_question="Do you have X-rays for this injury?",
        )
        self.assertEqual(result, {"has_reports": True, "wants_upload": False})

    def test_transcribed_report_word_uses_question_context(self):
        def unexpected_llm(_prompt):
            self.fail("Direct doctor-sharing response to an X-ray question is clear")

        result = classify_reports_intent(
            "I'll share my exercise directly with the doctor; I don't want to upload here.",
            unexpected_llm,
            context_question="Regarding the X-rays for your injury, can you upload them?",
        )
        self.assertEqual(result, {"has_reports": True, "wants_upload": False})

    def test_upload_decline_wins_over_the_word_upload(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["awaiting_report_upload"] = True
        state["user_input"] = (
            "I have X-rays but I don't want to upload them. "
            "I will share them directly with the doctor."
        )

        result = make_handle_upload_response_node()(state)

        self.assertFalse(result["awaiting_report_upload"])
        self.assertFalse(result["reports_uploaded"])
        self.assertFalse(result["request_attachment"])
        self.assertIn(
            "share them directly",
            result["form"]["History & Diagnostics"]["Reports"],
        )

    def test_answered_lifestyle_and_reports_question_is_detected(self):
        form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
        form["History & Diagnostics"]["Current Lifestyle"] = (
            "Smokes, does not drink alcohol, and walks for exercise"
        )
        form["History & Diagnostics"]["Reports"] = (
            "Has X-rays; will share directly with clinician"
        )
        self.assertTrue(
            asks_about_answered_field(
                "Could you tell me more about smoking and your X-rays?", form
            )
        )

    def test_extract_persists_decline_and_discards_repeated_prefetch(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        state["current_section"] = "History & Diagnostics"
        state["history"] = [
            {
                "role": "agent",
                "message": "Do you smoke or drink, what exercise do you do, and do you have X-rays?",
            }
        ]
        state["user_input"] = (
            "I smoke but don't drink. I only walk. I have the X-rays and will "
            "share them directly with the doctor; I don't want to upload here."
        )

        reasoned_form = state["form"].copy()
        reasoned_form["History & Diagnostics"] = dict(
            reasoned_form["History & Diagnostics"]
        )
        reasoned_form["History & Diagnostics"]["Current Lifestyle"] = (
            "Smokes, does not drink alcohol, and walks for exercise"
        )

        def reasoning_llm(_prompt):
            import json
            return json.dumps(reasoned_form)

        def llm_complete(_prompt):
            return (
                "THINKING: ask for the details\nQUESTION: Could you tell me more "
                "about your smoking habits and X-rays?"
            )

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(llm_complete, reasoning_llm=reasoning_llm)(state)

        self.assertEqual(
            result["reports_intent"],
            {"has_reports": True, "wants_upload": False},
        )
        self.assertIn(
            "declined in-app upload",
            result["form"]["History & Diagnostics"]["Reports"],
        )
        lifestyle = result["form"]["History & Diagnostics"]["Current Lifestyle"]
        self.assertIn("smokes", lifestyle.lower())
        self.assertIn("does not drink alcohol", lifestyle.lower())
        self.assertIn("walks for exercise", lifestyle.lower())
        self.assertIsNone(result["pending_question"])


if __name__ == "__main__":
    unittest.main()
