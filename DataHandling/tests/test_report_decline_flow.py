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
    def test_summary_recognizes_uploaded_documents_without_provider_call(self):
        from src.graph.pure_functions.summary import classify_summary_response
        result = classify_summary_response(
            "I have uploaded my clinical documents.", "summary",
            lambda _: self.fail("Upload notification must be deterministic"),
        )
        self.assertEqual(result["intent"], "has_reports")
        self.assertTrue(result["upload_claimed"])

    def test_summary_acknowledges_verified_attachments_without_reprompt(self):
        from src.graph.nodes.summary import make_handle_summary_response_node
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "summary"
        state["reports_uploaded"] = True
        state["summary_intent"] = {"intent": "has_reports", "wants_upload": True}
        result = make_handle_summary_response_node(lambda _: "unused")(state)
        self.assertFalse(result["awaiting_report_upload"])
        self.assertFalse(result["request_attachment"])
        self.assertIn("already attached", result["response_text"])

    def test_upload_claim_without_attachment_does_not_fake_receipt(self):
        from src.graph.nodes.summary import make_handle_summary_response_node
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["summary_intent"] = {"intent": "has_reports", "upload_claimed": True}
        result = make_handle_summary_response_node(lambda _: "unused")(state)
        self.assertFalse(result.get("reports_uploaded", False))
        self.assertFalse(result["request_attachment"])
        self.assertIn("cannot verify", result["response_text"])

    def test_adapter_checks_saved_attachments_outside_upload_wait(self):
        from src.graph.server_adapter import build_graph_state
        calls = []
        def fetch(form_id, user_id, attempt_id):
            calls.append((form_id, user_id, attempt_id))
            return {"attachments": [{"key": "report.pdf"}]}
        client = {"user_id": "u", "form_id": "FRM-01", "attempt_id": "ATT-test",
                  "graph_phase": "summary", "graph_awaiting_report_upload": False,
                  "_fetch_form_fn": fetch}
        result = build_graph_state(client, "I have uploaded my clinical documents.", lambda **_: None)
        self.assertTrue(result["reports_uploaded"])
        self.assertEqual(calls, [("FRM-01", "u", "ATT-test")])

    def test_doctor_consultation_confirmation_is_not_report_confirmation(self):
        def unexpected_llm(_prompt):
            self.fail("An unrelated consultation answer must not trigger report classification")

        result = classify_reports_intent(
            "Actually from last day I have experiencing this issue. It started suddenly. "
            "I think the screen time caused it. Yes I have seen the doctor. "
            "He suggested to reduce the screen time. Okay.",
            unexpected_llm,
            context_question="Have you seen a doctor or physiotherapist? What advice did they give?",
        )
        self.assertEqual(result, {})

    def test_short_confirmation_needs_report_question_context(self):
        self.assertEqual(classify_reports_intent("yes i have", lambda _: "unused", "Have you seen a doctor?"), {})
        self.assertEqual(
            classify_reports_intent("yes i have", lambda _: "unused", "Do you have related X-rays?"),
            {"has_reports": True, "wants_upload": None},
        )

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
