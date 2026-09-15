import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.graph.nodes.correction import make_apply_correction_node
from src.graph.nodes.summary import make_handle_summary_response_node
from src.graph.pure_functions.form_extraction import apply_form_correction
from src.graph.pure_functions.summary import classify_summary_response


def _load_edges_without_langgraph():
    package = types.ModuleType("langgraph")
    package.__path__ = []
    graph_module = types.ModuleType("langgraph.graph")
    graph_module.END = "__end__"
    path = Path(__file__).resolve().parents[1] / "src" / "graph" / "edges.py"
    spec = importlib.util.spec_from_file_location("_summary_correction_edges", path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"langgraph": package, "langgraph.graph": graph_module}):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


class SummaryCorrectionFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.edges = _load_edges_without_langgraph()

    def test_vague_change_request_does_not_call_llm(self):
        def unexpected_llm_call(_prompt):
            self.fail("A vague change request should be handled deterministically")

        result = classify_summary_response(
            "I would like to make any changes", "summary", unexpected_llm_call
        )

        self.assertEqual(result["intent"], "request_change")
        self.assertIsNone(result["correction_text"])

    def test_vague_change_request_asks_for_details_and_stops(self):
        state = {
            "summary_intent": {
                "intent": "request_change",
                "correction_text": None,
            },
            "history": [{"role": "agent", "message": "summary"}],
        }

        patch = make_handle_summary_response_node(lambda _prompt: "")(state)
        routed_state = {**state, **patch}

        self.assertIn("what should the correct information be", patch["response_text"])
        self.assertEqual(self.edges.after_handle_summary_response(routed_state), "__end__")

    def test_specific_change_routes_through_detection_before_application(self):
        state = {
            "summary_intent": {
                "intent": "request_change",
                "correction_text": "The pain is in my left knee, not my right knee",
            }
        }

        self.assertEqual(self.edges.after_handle_summary_response(state), "detect_correction")

    def test_failed_correction_is_not_hidden_by_a_regenerated_summary(self):
        state = {
            "correction_data": None,
            "history": [{"role": "user", "message": "change my pain"}],
            "phase": "summary",
        }

        patch = make_apply_correction_node(lambda _prompt: "")(state)
        routed_state = {**state, **patch}

        self.assertFalse(patch["correction_applied"])
        self.assertIn("exact change", patch["response_text"])
        self.assertEqual(self.edges.after_apply_correction(routed_state), "__end__")

    def test_successful_summary_correction_regenerates_summary(self):
        self.assertEqual(
            self.edges.after_apply_correction(
                {"correction_applied": True, "phase": "summary"}
            ),
            "generate_summary",
        )

    def test_application_changes_only_the_identified_field(self):
        original = {
            "Present Complaint": {
                "Location": "right knee",
                "Duration": "two days",
            },
            "Referral": {"Source": "Google"},
        }
        correction = {
            "is_correction": True,
            "section_name": "Present Complaint",
            "field_name": "Location",
            "old_value": "right knee",
            "new_value": "left knee",
            "needs_clarification": False,
        }

        def unexpected_llm_call(_prompt):
            self.fail("Applying a validated field correction must not call the LLM")

        updated, success, _message = apply_form_correction(
            correction, original, unexpected_llm_call
        )

        self.assertTrue(success)
        self.assertEqual(updated["Present Complaint"]["Location"], "left knee")
        self.assertEqual(updated["Present Complaint"]["Duration"], "two days")
        self.assertEqual(updated["Referral"]["Source"], "Google")
        self.assertEqual(original["Present Complaint"]["Location"], "right knee")
        self.assertNotEqual(updated, original)

    def test_empty_replacement_value_is_rejected(self):
        form = {"Present Complaint": {"Location": "right knee"}}
        correction = {
            "section_name": "Present Complaint",
            "field_name": "Location",
            "new_value": "   ",
        }

        updated, success, message = apply_form_correction(correction, form, lambda _: "")

        self.assertFalse(success)
        self.assertEqual(updated, form)
        self.assertIn("correct information", message)


if __name__ == "__main__":
    unittest.main()
