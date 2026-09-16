import sys
import types
import unittest
from unittest.mock import patch

from src.graph.pure_functions.clarification import build_intake_clarification
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


class IntakeClarificationTests(unittest.TestCase):
    def test_surgery_clarification_explains_requested_details(self):
        response = build_intake_clarification(
            "What basically do you want to know about my surgery?",
            "Could you tell me more about your leg surgery?",
        )

        self.assertIn("what procedure", response)
        self.assertIn("which body part", response)
        self.assertIn("approximately when", response)
        self.assertIn("complications", response)

    def test_ordinary_surgery_answer_is_not_intercepted(self):
        self.assertIsNone(
            build_intake_clarification(
                "I had ligament surgery on my right knee two years ago.",
                "Could you tell me more about your surgery?",
            )
        )

    def test_mixed_question_preserves_extraction_and_returns_explanation(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        state["current_section"] = "History & Diagnostics"
        state["history"] = [
            {
                "role": "agent",
                "message": "Tell me about your surgery, other conditions, and exercise habits.",
            }
        ]
        state["user_input"] = (
            "What do you want to know about my surgery? I had jaundice last year "
            "and I work out once a week."
        )

        reasoned_form = state["form"].copy()
        reasoned_form["History & Diagnostics"] = dict(
            reasoned_form["History & Diagnostics"]
        )
        reasoned_form["History & Diagnostics"][
            "Systemic Illness and Surgical History"
        ] = "Jaundice last year"
        reasoned_form["History & Diagnostics"]["Current Lifestyle"] = (
            "Works out once a week"
        )

        def reasoning_llm(_prompt):
            import json
            return json.dumps(reasoned_form)

        def llm_complete(prompt):
            if "diagnostic reports" in prompt:
                return '{"has_reports": null, "wants_upload": null}'
            return "THINKING: surgery details remain unclear\nQUESTION: Tell me more about the surgery."

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(llm_complete, reasoning_llm=reasoning_llm)(state)

        self.assertTrue(result["direct_response_handled"])
        self.assertIn("what procedure", result["response_text"])
        self.assertEqual(
            result["form"]["History & Diagnostics"]["Current Lifestyle"],
            "Works out once a week",
        )
        self.assertEqual(result["history"][-1]["role"], "agent")
        self.assertEqual(result["history"][-1]["message"], result["response_text"])


if __name__ == "__main__":
    unittest.main()
