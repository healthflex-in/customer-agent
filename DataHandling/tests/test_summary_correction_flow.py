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
    def test_head_pain_too_is_saved_as_head_not_previous_leg(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(phase="summary", user_input="I have head pain too")
        state["form"]["Present Complaint"]["Primary Complaint"] = "Leg pain"
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Leg"

        def unexpected(_):
            self.fail("Explicit additional head pain must not call AI")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        self.assertEqual(state["summary_intent"]["intent"], "new_complaint")
        result = make_handle_summary_response_node(unexpected)(state)
        saved = result["form"]["Additional Complaint 1"]["Primary Complaint"]
        self.assertIn("head pain", saved.lower())
        self.assertNotIn("leg pain", saved.lower())

    def test_combined_severity_update_and_new_complaint_apply_together(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.nodes.summary import make_classify_summary_intent_node

        for text in (
            "Please update pain to 7 and I also have leg pain",
            "Please change head pain severity to 7, and I also have leg pain",
            "Can you please increase it to 10 on scale the head pain, and I also have hand pain",
        ):
            with self.subTest(text=text):
                state = get_fresh_interview_state("u", "FRM-01", "s")
                state.update(phase="summary", user_input=text)
                state["form"]["Present Complaint"]["Primary Complaint"] = "Head pain"
                state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Head"
                state["form"]["Pain Assessment"]["Severity (1-10)"] = "5/10"

                def unexpected_llm(_):
                    self.fail("Clear combined actions must not require an AI call")

                state.update(make_classify_summary_intent_node(unexpected_llm)(state))
                result = make_handle_summary_response_node(unexpected_llm)(state)

                expected_score = "10/10" if "10 on scale" in text else "7/10"
                expected_addition = "hand pain" if "hand pain" in text else "leg pain"
                self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], expected_score)
                self.assertEqual(
                    result["form"]["Additional Complaint 1"]["Primary Complaint"].lower(),
                    expected_addition,
                )
                self.assertEqual(result["form"]["Additional Complaint 1"]["Severity (1-10)"], "")
                self.assertIn("updated", result["response_text"].lower())
                self.assertIn("added", result["response_text"].lower())
                self.assertIn(expected_score, result["response_text"])
                self.assertEqual(result["phase"], "interviewing")

    def test_existing_additional_complaint_label_is_corrected_not_duplicated(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(phase="summary", user_input=(
            "Can you please change the additional point, it is only hand pain only"
        ))
        state["form"]["Additional Complaint 1"] = {
            "Primary Complaint": "the whole correction sentence",
            "Duration of the Issue": "one day",
        }
        state["form_sections"].append("Additional Complaint 1")

        def unexpected(_):
            self.fail("Explicit additional-complaint correction must be deterministic")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        result = make_handle_summary_response_node(unexpected)(state)

        self.assertEqual(
            result["form"]["Additional Complaint 1"]["Primary Complaint"],
            "hand pain",
        )
        self.assertNotIn("Additional Complaint 2", result["form"])
        self.assertIn("corrected", result["response_text"].lower())

    def test_qa_combined_leg_score_correction_and_hand_complaint(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="summary",
            user_input=(
                "can you pls increase it to 10 on scale the leg pain, "
                "I also have hand pain"
            ),
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Leg pain"
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Leg"
        state["form"]["Pain Assessment"]["Severity (1-10)"] = "9/10"

        def unexpected(_):
            self.fail("The exact QA correction must be deterministic")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        result = make_handle_summary_response_node(unexpected)(state)

        self.assertEqual(
            result["form"]["Pain Assessment"]["Severity (1-10)"], "10/10"
        )
        self.assertEqual(
            result["form"]["Additional Complaint 1"]["Primary Complaint"],
            "hand pain",
        )

    def test_generated_summary_cannot_omit_added_pain(self):
        from src.graph.pure_functions.summary import generate_interview_summary
        text = "I have pain in my hand as well"
        form = {
            "Present Complaint": {"Primary Complaint": "Head pain"},
            "Additional Complaint 1": {"Primary Complaint": text},
        }
        response = generate_interview_summary(
            form, [], lambda _: "You have head pain.\nKey points:\n- Head pain.\nIs this information correct, or would you like to make any changes?"
        )
        self.assertIn(text, response)
        self.assertEqual(response.count("Is this information correct"), 1)

    def test_additional_pain_is_saved_acknowledged_and_requires_followup(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.nodes.summary import make_classify_summary_intent_node
        from src.graph.nodes.generate import required_response_before_summary
        from src.graph.server_adapter import sync_client_state_from_graph, build_graph_state
        for text in (
            "I want to can you add some info for me like I have pain in my hand as well. I forgot to tell you can you please add that.",
            "I actually I want to oh let you know that I have pain in my arms as well. Okay.",
            "I want to update information I have pain in my hand aswell",
        ):
            with self.subTest(text=text):
                state = get_fresh_interview_state("u", "FRM-01", "s")
                state["phase"] = "summary"
                for fields in state["form"].values():
                    for field in fields:
                        fields[field] = "Original patient answer"
                state["form"]["Present Complaint"]["Primary Complaint"] = "Head pain"
                state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Head"
                original = {key: dict(value) for key, value in state["form"].items()}
                state["user_input"] = text
                def unexpected_llm(_):
                    self.fail("An explicit symptom addition must not require a classifier call")
                intent = make_classify_summary_intent_node(unexpected_llm)(state)
                state.update(intent)
                self.assertEqual(state["summary_intent"]["intent"], "new_complaint")
                result = make_handle_summary_response_node(unexpected_llm)(state)
                updated = {**state, **result}
                for section, fields in original.items():
                    self.assertEqual(updated["form"][section], fields)
                self.assertEqual(updated["form"]["Additional Complaint 1"]["Primary Complaint"], text)
                self.assertIn("I've added", result["response_text"])
                self.assertNotIn("Key points", result["response_text"])
                self.assertEqual(self.edges.after_handle_summary_response(updated), "__end__")
                client = {}
                sync_client_state_from_graph(client, updated)
                restored = build_graph_state(client, "ok done", lambda **_: None)
                self.assertIn("Additional Complaint 1", restored["form_sections"])
                gate = required_response_before_summary(restored, restored["history"])
                self.assertIsNotNone(gate)
                self.assertEqual(gate["phase"], "interviewing")
                self.assertIn("Duration of the Issue", gate["missing_fields"])

    def test_summary_classification_uses_current_facts_after_correction(self):
        from src.graph.nodes.summary import make_classify_summary_intent_node
        prompts = []
        def llm(prompt):
            prompts.append(prompt)
            return '{"intent": "question", "correction_text": null}'
        state = {
            "form": {"Pain Assessment": {"Primary Location of Pain": "Head only"}},
            "user_input": "What have you recorded now?",
            "history": [{"role": "agent", "message": "Key points: resolve leg and arm pain. Is this information correct?"}],
        }
        result = make_classify_summary_intent_node(llm)(state)
        self.assertIn("Head only", prompts[0])
        self.assertNotIn("resolve leg and arm pain", prompts[0])
        self.assertEqual(result["history"][-1], {"role": "user", "message": state["user_input"]})

    def test_head_correction_then_approval_requires_cleared_answer(self):
        from src.graph.state import get_fresh_interview_state
        from src.graph.pure_functions.form_extraction import detect_form_correction
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "summary"
        for fields in state["form"].values():
            for field in fields:
                fields[field] = "Patient provided answer"
        state["form"]["Present Complaint"]["Primary Complaint"] = "Leg and arm pain"
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Leg and arm"
        state["form"]["Pain Assessment"]["Aggravating Factors"] = "Moving my hand and leg"
        state["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"] = "Resolve leg and arm pain"
        state["user_input"] = "And I dont have any pain in my legs and arms. I have pain in my head only."
        state["correction_data"] = detect_form_correction(state["user_input"], state["form"], True, lambda _: "unused")
        update = make_apply_correction_node(lambda _: "unused")(state)
        self.assertTrue(update["correction_applied"])
        self.assertIn("update anything else", update["response_text"])
        self.assertNotIn("Key points", update["response_text"])
        self.assertEqual(self.edges.after_apply_correction({**state, **update}), "__end__")
        corrected = {**state, **update, "summary_intent": {"intent": "confirm"}, "user_input": "ok done"}
        approval = make_handle_summary_response_node(lambda _: "unused")(corrected)
        self.assertEqual(approval["phase"], "interviewing")
        self.assertNotIn("recorded", approval["response_text"])
        self.assertIn("Aggravating Factors", approval["missing_fields"])
        self.assertNotIn("leg", corrected["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"].lower())

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

    def test_successful_summary_correction_confirms_change_without_full_summary(self):
        self.assertEqual(
            self.edges.after_apply_correction(
                {"correction_applied": True, "phase": "summary"}
            ),
            "__end__",
        )

    def test_done_after_correction_confirms_without_llm(self):
        def unexpected_llm_call(_prompt):
            self.fail("A clear final approval must not call the LLM")

        result = classify_summary_response("ok done", "summary", unexpected_llm_call)
        self.assertEqual(result["intent"], "confirm")

    def test_pain_location_correction_reconciles_dependent_fields(self):
        from src.graph.pure_functions.form_extraction import detect_form_correction

        form = {
            "Present Complaint": {
                "Primary Complaint": "Pain in leg and arm",
                "Mechanism of Injury or Cause": "Unknown",
            },
            "Pain Assessment": {
                "Primary Location of Pain": "Leg and arm",
                "Severity (1-10)": "6",
                "Aggravating Factors": "Moving my hand and leg makes it worse",
                "Relieving Factors": "Rest helps",
            },
            "Treatment Goals": {
                "Short-Term Goals (within 3 months)": "Resolve pain in leg and arm",
                "Long-Term Goals (after 3 months)": "Return to normal activity",
                "Specific Expectations from Treatment": "Treat leg and arm pain",
            },
        }

        def unexpected_llm_call(_prompt):
            self.fail("The explicit pain-location correction must be deterministic")

        correction = detect_form_correction(
            "I don't have any pain in my legs and arms. I have pain in my head only.",
            form,
            is_summary_mode=True,
            llm_complete=unexpected_llm_call,
        )
        updated, success, message = apply_form_correction(
            correction, form, unexpected_llm_call
        )

        self.assertTrue(success)
        self.assertEqual(
            updated["Pain Assessment"]["Primary Location of Pain"], "Head only"
        )
        self.assertEqual(updated["Pain Assessment"]["Aggravating Factors"], "")
        self.assertEqual(
            updated["Present Complaint"]["Primary Complaint"], "Pain in head only"
        )
        self.assertNotIn(
            "leg",
            updated["Treatment Goals"]["Short-Term Goals (within 3 months)"].lower(),
        )
        self.assertNotIn(
            "arm",
            updated["Treatment Goals"]["Specific Expectations from Treatment"].lower(),
        )
        self.assertIn("update anything else", message)

    def test_pain_location_replacement_routes_as_correction_without_llm(self):
        def unexpected_llm_call(_prompt):
            self.fail("Explicit old/new pain locations must not call the LLM")

        text = (
            "I don't have any pain in my legs and arms. "
            "I have pain in my head only."
        )
        intent = classify_summary_response(text, "summary", unexpected_llm_call)
        self.assertEqual(intent["intent"], "request_change")
        self.assertEqual(intent["correction_text"], text)

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

    def test_summary_can_be_reshared_without_advice_refusal_or_ai(self):
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = {
            "user_input": "can you please share summary again which you have written so I can verify",
            "form": {
                "Present Complaint": {"Primary Complaint": "Knee pain"},
                "Referral": {"Source": "Google"},
            },
            "history": [{"role": "agent", "message": "Is this correct?"}],
        }

        def unexpected(_prompt):
            self.fail("A summary replay must not call the AI classifier")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        self.assertEqual(state["summary_intent"]["intent"], "show_summary")
        result = make_handle_summary_response_node(unexpected)(state)
        self.assertIn("Knee pain", result["response_text"])
        self.assertIn("Google", result["response_text"])
        self.assertNotIn("medical advice", result["response_text"].lower())

    def test_additional_complaint_delete_never_edits_primary_complaint(self):
        from src.graph.nodes.summary import make_classify_summary_intent_node

        for text in (
            "I dont have any hand pain please remove",
            "remove this dont need additional complaint",
            "I do not have hand pain",
        ):
            with self.subTest(text=text):
                state = {
                    "user_input": text,
                    "phase": "summary",
                    "current_section": "Additional Complaint 1",
                    "form_sections": ["Present Complaint", "Additional Complaint 1"],
                    "form": {
                        "Present Complaint": {"Primary Complaint": "Knee pain"},
                        "Additional Complaint 1": {
                            "Primary Complaint": "Hand pain",
                            "Severity (1-10)": "5/10",
                        },
                    },
                    "history": [{"role": "agent", "message": "Is this correct?"}],
                }

                def unexpected(_prompt):
                    self.fail("An explicit deletion must not call AI")

                state.update(make_classify_summary_intent_node(unexpected)(state))
                self.assertEqual(
                    state["summary_intent"]["intent"],
                    "remove_additional_complaint",
                )
                result = make_handle_summary_response_node(unexpected)(state)
                self.assertNotIn("Additional Complaint 1", result["form"])
                self.assertEqual(
                    result["form"]["Present Complaint"]["Primary Complaint"],
                    "Knee pain",
                )
                self.assertNotIn("don't have hand pain", str(result["form"]).lower())
                self.assertIn("removed", result["response_text"].lower())

    def test_additional_complaint_wording_updates_only_additional_section(self):
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = {
            "user_input": (
                "you have written additional point right in that write forearm and hand"
            ),
            "phase": "summary",
            "current_section": "Additional Complaint 1",
            "form": {
                "Present Complaint": {"Primary Complaint": "Knee pain"},
                "Additional Complaint 1": {"Primary Complaint": "Arm pain"},
            },
            "history": [{"role": "agent", "message": "Is this correct?"}],
        }

        def unexpected(_prompt):
            self.fail("An explicit additional-complaint edit must not call AI")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        result = make_handle_summary_response_node(unexpected)(state)
        self.assertEqual(
            result["form"]["Additional Complaint 1"]["Primary Complaint"],
            "forearm and hand pain",
        )
        self.assertEqual(
            result["form"]["Present Complaint"]["Primary Complaint"],
            "Knee pain",
        )

    def test_removal_never_deletes_a_different_complaint(self):
        from src.graph.pure_functions.additional_complaint import (
            additional_complaint_removal_target,
        )

        form = {
            "Present Complaint": {"Primary Complaint": "Knee pain"},
            "Additional Complaint 1": {"Primary Complaint": "Hand pain"},
        }
        self.assertIsNone(
            additional_complaint_removal_target(
                "remove the knee pain", form, "Additional Complaint 1"
            )
        )


if __name__ == "__main__":
    unittest.main()
