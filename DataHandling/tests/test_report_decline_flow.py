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

    def test_compact_lifestyle_answer_fills_every_subtopic(self):
        from src.graph.pure_functions.lifestyle import (
            format_lifestyle_value,
            missing_lifestyle_components,
        )

        value = merge_lifestyle_answer(
            "", "4 times a week, work in company, yes smoke, yes drink"
        )

        self.assertEqual(missing_lifestyle_components(value), [])
        self.assertIn("Work:", value)
        self.assertIn("Activity/exercise:", value)
        self.assertIn("Smoking: Smokes", value)
        self.assertIn("Alcohol: Drinks alcohol", value)
        self.assertEqual(
            format_lifestyle_value({
                "Work": "Works in a company",
                "Activity/exercise": "No exercise routine",
                "Smoking": "Smokes",
                "Alcohol": "Drinks alcohol",
            }),
            "Work: Works in a company; Activity/exercise: No exercise routine; "
            "Smoking: Smokes; Alcohol: Drinks alcohol",
        )
        self.assertEqual(
            format_lifestyle_value(
                "{'Work': 'Works in a company', 'Smoking': 'Smokes'}"
            ),
            "Work: Works in a company; Smoking: Smokes",
        )

    def test_alcohol_correction_replaces_contradictory_old_value(self):
        existing = (
            "Work: Works in a company; Activity/exercise: Yoga; Smoking: Smokes; "
            "Alcohol: Does not drink alcohol"
        )
        value = merge_lifestyle_answer(
            existing,
            "can you please update I drink also and pain rating is 10",
        )

        self.assertIn("Alcohol: Drinks alcohol", value)
        self.assertNotIn("Alcohol: Does not drink alcohol", value)
        self.assertEqual(value.count("Alcohol:"), 1)

    def test_compound_work_and_alcohol_answer_is_split_into_correct_fields(self):
        for answer in (
            "I work in a company , no drink",
            "I work in a company and I don't drink",
            "I work in a company no drink",
        ):
            with self.subTest(answer=answer):
                value = merge_lifestyle_answer(
                    "",
                    answer,
                    "What is your occupation, and do you drink alcohol?",
                )

                self.assertIn("Work: I work in a company", value)
                self.assertIn("Alcohol: Does not drink alcohol", value)
                self.assertNotIn("drink", value.split(";", 1)[0].lower())
                self.assertEqual(value.count("Work:"), 1)
                self.assertEqual(value.count("Alcohol:"), 1)

    def test_bare_job_title_answers_occupation_question(self):
        value = merge_lifestyle_answer(
            "",
            "accountant",
            "What is your work or usual occupation?",
        )

        self.assertEqual(value, "Work: accountant")

    def test_health_condition_denial_in_batch_is_not_saved_as_work(self):
        question = (
            "Could you also tell me:\n"
            "- Do you have any other health conditions, past surgeries, or fractures?\n"
            "- What is your work and usual activity or exercise routine?"
        )
        for answer in ("no other conditions", "noter contions"):
            with self.subTest(answer=answer):
                value = merge_lifestyle_answer("", answer, question)
                self.assertEqual(value, "")

    def test_unpunctuated_exercise_and_smoking_are_separate_components(self):
        value = merge_lifestyle_answer(
            "",
            "I exercise three days in a week I smoke",
            "What is your exercise routine, and do you smoke?",
        )

        self.assertIn(
            "Activity/exercise: I exercise three days in a week",
            value,
        )
        self.assertIn("Smoking: Smokes", value)
        activity = next(
            part for part in value.split(";")
            if part.strip().startswith("Activity/exercise:")
        )
        self.assertNotIn("smoke", activity.lower())

    def test_existing_cross_field_lifestyle_data_is_repaired_on_read(self):
        from src.graph.pure_functions.lifestyle import (
            format_lifestyle_value,
            missing_lifestyle_components,
        )

        polluted = {
            "Work": "noter contions",
            "Activity/exercise": "I exercise three days in a week I smoke",
            "Smoking": "Smokes",
            "Alcohol": "Does not drink alcohol",
        }
        value = format_lifestyle_value(polluted)

        self.assertNotIn("noter contions", value)
        self.assertIn("Activity/exercise: I exercise three days in a week", value)
        self.assertNotIn("week I smoke", value)
        self.assertIn("Smoking: Smokes", value)
        self.assertIn(
            "Current Lifestyle — Work",
            missing_lifestyle_components(value),
        )

    def test_existing_labelled_string_is_repaired_before_next_merge(self):
        polluted = (
            "Work: no other conditions; "
            "Activity/exercise: I exercise three days in a week I smoke; "
            "Smoking: Smokes"
        )

        value = merge_lifestyle_answer(polluted, "I do not drink alcohol")

        self.assertNotIn("Work:", value)
        self.assertNotIn("week I smoke", value)
        self.assertIn("Smoking: Smokes", value)
        self.assertIn("Alcohol: Does not drink alcohol", value)

    def test_reasoning_model_cannot_store_health_denial_as_occupation(self):
        import copy
        import json

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="History & Diagnostics",
            user_input="noter contions",
            history=[{
                "role": "agent",
                "message": (
                    "Could you also tell me:\n"
                    "- Do you have any other health conditions?\n"
                    "- What is your work and usual activity or exercise routine?"
                ),
            }],
        )
        reasoned = copy.deepcopy(state["form"])
        reasoned["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: noter contions"
        )

        def reasoning_llm(_prompt):
            return json.dumps(reasoned)

        with patch(
            "src.graph.nodes.extract.get_stream_writer",
            return_value=lambda _event: None,
        ):
            result = make_extract_node(
                lambda _prompt: "{}",
                reasoning_llm=reasoning_llm,
            )(state)

        self.assertEqual(
            result["form"]["History & Diagnostics"]["Current Lifestyle"],
            "",
        )

    def test_reasoning_extracted_occupation_is_not_discarded(self):
        import copy
        import json

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="History & Diagnostics",
            user_input="accountant",
            history=[{
                "role": "agent",
                "message": "What is your work or usual occupation?",
            }],
        )
        reasoned = copy.deepcopy(state["form"])
        reasoned["History & Diagnostics"]["Current Lifestyle"] = "Accountant"

        def reasoning_llm(_prompt):
            return json.dumps(reasoned)

        with patch(
            "src.graph.nodes.extract.get_stream_writer",
            return_value=lambda _event: None,
        ):
            result = make_extract_node(
                lambda _prompt: "{}",
                reasoning_llm=reasoning_llm,
            )(state)

        lifestyle = result["form"]["History & Diagnostics"]["Current Lifestyle"]
        self.assertEqual(lifestyle, "Work: accountant")
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components
        self.assertNotIn(
            "Current Lifestyle — Work",
            missing_lifestyle_components(lifestyle),
        )

    def test_pain_aggravation_answer_cannot_enter_lifestyle(self):
        existing = (
            "Work: Works in a company; Activity/exercise: Yoga; Smoking: Smokes; "
            "Alcohol: Does not drink alcohol"
        )
        value = merge_lifestyle_answer(
            existing,
            "when I walk increases pain and massage provide relief",
            "What makes the pain worse, and what provides relief?",
        )

        self.assertEqual(value, existing)
        self.assertNotIn("Walks for exercise", value)

    def test_extractor_cannot_write_pain_answer_into_lifestyle(self):
        import copy
        import json

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="Pain Assessment",
            user_input="when I walk increases pain and massage provide relief",
            history=[{
                "role": "agent",
                "message": "What makes the pain worse, and what provides relief?",
            }],
        )
        existing = "Work: Company; Activity/exercise: Yoga; Smoking: Does not smoke"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = existing
        contaminated = copy.deepcopy(state["form"])
        contaminated["Pain Assessment"]["Aggravating Factors"] = "Walking"
        contaminated["Pain Assessment"]["Relieving Factors"] = "Massage"
        contaminated["History & Diagnostics"]["Current Lifestyle"] = (
            "Activity/exercise: hen I walk increases pain and massage provide relief; "
            "Activity/exercise: Walks for exercise"
        )

        def reasoning_llm(_prompt):
            return json.dumps(contaminated)

        def llm_complete(_prompt):
            return '{}'

        with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
            result = make_extract_node(llm_complete, reasoning_llm=reasoning_llm)(state)

        self.assertEqual(
            result["form"]["History & Diagnostics"]["Current Lifestyle"],
            existing,
        )
        self.assertEqual(
            result["form"]["Pain Assessment"]["Aggravating Factors"], "Walking"
        )
        self.assertEqual(
            result["form"]["Pain Assessment"]["Relieving Factors"], "Massage"
        )

    def test_combined_alcohol_and_rating_update_applies_both_not_new_complaint(self):
        from src.graph.nodes.correction import (
            make_apply_correction_node,
            make_detect_correction_node,
        )
        from src.graph.nodes.summary import make_classify_summary_intent_node

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="summary",
            user_input="can you please update I drink also and pain rating is 10",
        )
        state["form"]["Present Complaint"]["Primary Complaint"] = "Leg pain"
        state["form"]["Pain Assessment"]["Primary Location of Pain"] = "Leg"
        state["form"]["Pain Assessment"]["Severity (1-10)"] = "9/10"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: Company; Activity/exercise: Yoga; Smoking: Smokes; "
            "Alcohol: Does not drink alcohol"
        )

        def unexpected(_):
            self.fail("Combined explicit updates must not call AI")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        self.assertEqual(state["summary_intent"]["intent"], "request_change")
        state.update(make_detect_correction_node(unexpected)(state))
        result = make_apply_correction_node(unexpected)(state)

        self.assertNotIn("Additional Complaint 1", result["form"])
        self.assertEqual(
            result["form"]["Pain Assessment"]["Severity (1-10)"], "10/10"
        )
        lifestyle = result["form"]["History & Diagnostics"]["Current Lifestyle"]
        self.assertIn("Alcohol: Drinks alcohol", lifestyle)
        self.assertNotIn("Alcohol: Does not drink alcohol", lifestyle)


if __name__ == "__main__":
    unittest.main()
