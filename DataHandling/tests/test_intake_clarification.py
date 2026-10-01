import sys
import types
import unittest
from unittest.mock import patch

from src.graph.pure_functions.clarification import (
    OUT_OF_FLOW_RESPONSE,
    build_activity_clearance_response,
    build_intake_clarification,
    build_out_of_flow_response,
)
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
    def test_return_to_sport_question_gets_safe_answer(self):
        for text in (
            "I just want to know can I return back to sports or not",
            "when i can get back to the sports",
            "Can I resume running?",
            "Is it safe to go back to the gym?",
            "while palming support can I get back and start palaying again",
        ):
            with self.subTest(text=text):
                response = build_activity_clearance_response(text)
                self.assertEqual(response, OUT_OF_FLOW_RESPONSE)

    def test_activity_statement_is_not_intercepted(self):
        self.assertIsNone(build_activity_clearance_response("I returned to sports last week"))

    def test_summary_replay_requests_are_not_blocked_as_medical_advice(self):
        for text in (
            "can you pls share the information you wrote again",
            "can you please share summary again which you have written so I can verify",
            "pls share the updated summary",
        ):
            with self.subTest(text=text):
                self.assertIsNone(build_out_of_flow_response(text))

    def test_global_boundary_covers_advice_medication_and_unrelated_questions(self):
        for text in (
            "Can I play soccer again? Can I take a painkiller and play?",
            "Should I take ibuprofen for this injury?",
            "What should I do about my knee injury?",
            "What could this injury be?",
            "Can you tell me what causes knee pain?",
            "Tell me a joke?",
            "Who won the soccer match?",
            "Can I dance tomorrow?",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    build_out_of_flow_response(text),
                    OUT_OF_FLOW_RESPONSE,
                )

    def test_clinical_answers_and_intake_clarifications_are_not_blocked(self):
        for text in (
            "I take ibuprofen occasionally.",
            "I returned to sports last week.",
            "My pain is 5 out of 10 and walking makes it worse.",
            "When I walk the pain increases and massage provides relief.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(build_out_of_flow_response(text))
        self.assertIsNone(build_out_of_flow_response(
            "Can you explain what you mean?",
            "Tell me about your usual exercise routine.",
        ))
        self.assertIsNone(build_out_of_flow_response("Can I upload my MRI report?"))
        self.assertIsNone(build_out_of_flow_response("Should I include an old surgery?"))

    def test_qa_intake_answers_navigation_and_corrections_are_not_blocked(self):
        allowed = (
            (
                "No past surgeries. I exercise two or three times a week. I don't "
                "smoke, I rarely drink alcohol, and I want stronger muscles so I can "
                "continue with sports."
            ),
            "Okay. What is the next question?",
            "Can you please increase the leg pain rating to 10? I also have hand pain.",
            (
                "What is the main problem bothering you? I have persistent lower back "
                "pain. How long has it lasted? Around three weeks. It started gradually "
                "after working long hours at my desk."
            ),
        )
        for text in allowed:
            with self.subTest(text=text):
                self.assertIsNone(build_out_of_flow_response(text))

    def test_return_to_sport_interrupt_does_not_repeat_or_extract(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="Present Complaint",
            user_input="I just want to know can I return back to sports or not",
            history=[{"role": "agent", "message": "How long has this issue lasted, and did it start suddenly?"}],
        )

        def unexpected(_prompt):
            self.fail("Activity-clearance interruption must not call an AI provider")

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(unexpected, reasoning_llm=unexpected)(state)

        self.assertTrue(result["direct_response_handled"])
        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))
        self.assertIn("How long", result["response_text"])
        self.assertNotIn("form", result)
        self.assertEqual(result["history"][-2]["role"], "user")
        self.assertEqual(result["history"][-1]["role"], "agent")
        self.assertIs(result["history"][-2]["clinical_extraction"], False)
        self.assertIs(result["history"][-1]["clinical_extraction"], False)

    def test_medication_question_returns_standard_boundary_without_ai(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="Pain Assessment",
            user_input="Can I take a painkiller and play soccer?",
            history=[{"role": "agent", "message": "What makes the pain worse?"}],
        )

        def unexpected(_prompt):
            self.fail("Out-of-flow boundary must not call an AI provider")

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(unexpected, reasoning_llm=unexpected)(state)

        self.assertTrue(result["direct_response_handled"])
        self.assertTrue(result["response_text"].startswith(OUT_OF_FLOW_RESPONSE))
        self.assertIn("What makes the pain worse?", result["response_text"])
        self.assertNotIn("form", result)
        self.assertIs(result["history"][-2]["clinical_extraction"], False)
        self.assertIs(result["history"][-1]["clinical_extraction"], False)

    def test_return_to_play_question_is_not_saved_as_mechanism(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="Present Complaint",
            user_input="while palming support can I get back and start palaying again",
            history=[{
                "role": "agent",
                "message": "What caused it or what was happening when it started?",
            }],
        )

        def unexpected(_prompt):
            self.fail("A patient question must be intercepted before extraction")

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(unexpected, reasoning_llm=unexpected)(state)

        self.assertTrue(result["direct_response_handled"])
        self.assertNotIn("form", result)
        self.assertIn("What caused it", result["response_text"])

    def test_active_additional_complaint_can_be_retracted_or_skipped(self):
        for text in (
            "there is no additional complaint",
            "I don't have any additional complaint",
            "lets skip this",
            "no need enough bye",
            "no need of additional complaint",
            "no need of additional compamnt",
            "no need of additional complaint by mistake i told u",
        ):
            with self.subTest(text=text):
                state = get_fresh_interview_state("u", "FRM-01", "s")
                state.update(
                    phase="interviewing",
                    current_section="Additional Complaint 2",
                    form_sections=[*state["form_sections"], "Additional Complaint 1", "Additional Complaint 2"],
                    user_input=text,
                )
                state["form"]["Additional Complaint 1"] = {
                    "Primary Complaint": "hand pain"
                }
                state["form"]["Additional Complaint 2"] = {
                    "Primary Complaint": "incorrect duplicate",
                    "Duration of the Issue": "",
                }

                def unexpected(_prompt):
                    self.fail("Retracting an accidental complaint must not call AI")

                with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
                    result = make_extract_node(unexpected, reasoning_llm=unexpected)(state)

                self.assertTrue(result["direct_response_handled"])
                self.assertEqual(result["phase"], "interviewing")
                self.assertNotIn("Additional Complaint 2", result["form"])
                self.assertIn("Additional Complaint 1", result["form"])
                self.assertNotIn(
                    "For this additional concern",
                    result["response_text"],
                )
                self.assertIn("remaining intake", result["response_text"])
                self.assertIn("removed", result["response_text"].lower())

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
        lifestyle = result["form"]["History & Diagnostics"]["Current Lifestyle"]
        self.assertIn("Activity/exercise:", lifestyle)
        self.assertIn("work out once a week", lifestyle.lower())
        self.assertEqual(result["history"][-1]["role"], "agent")
        self.assertEqual(result["history"][-1]["message"], result["response_text"])


if __name__ == "__main__":
    unittest.main()
