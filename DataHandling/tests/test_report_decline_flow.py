import sys
import types
import unittest
from unittest.mock import patch

from src.graph.nodes.upload import make_handle_upload_response_node
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


def mapping_llm(answers=(), reports=None):
    """Fake mapper model returning one structured mapping payload."""
    import json

    payload = json.dumps({
        "visit_context": "specific_complaint",
        "answers": list(answers),
        "reports": reports or {"has_reports": None, "wants_upload": None},
    })
    return lambda _prompt: payload


def run_extract(state, mapper):
    with patch("src.graph.nodes.extract.get_stream_writer", return_value=lambda _event: None):
        return make_extract_node(
            lambda _prompt: "{}", reasoning_llm=mapper
        )(state)


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

    def test_unasked_lifestyle_words_are_not_assigned_by_phrase(self):
        value = merge_lifestyle_answer(
            "",
            "Actually I smoke only, I don't drink alcohol. I only walk and "
            "I don't have any other exercise routine.",
        )
        self.assertEqual(value, "")

    def retired_shared_habit_negation_and_common_typo_fill_both_fields(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components

        for answer in (
            "I dnt smoke or drink.",
            "I don't smoke or drink alcohol.",
            "I do not drink or smoke.",
        ):
            with self.subTest(answer=answer):
                value = merge_lifestyle_answer(
                    "", answer, "Do you smoke or drink alcohol?"
                )
                self.assertIn(f"Smoking: {answer}", value)
                self.assertIn(f"Alcohol: {answer}", value)
                self.assertNotIn(
                    "Current Lifestyle — Smoking",
                    missing_lifestyle_components(value),
                )
                self.assertNotIn(
                    "Current Lifestyle — Alcohol",
                    missing_lifestyle_components(value),
                )

    def retired_short_habit_denial_uses_active_question_context(self):
        smoking = merge_lifestyle_answer(
            "", "Never.", "Do you smoke or use tobacco?"
        )
        alcohol = merge_lifestyle_answer(
            smoking, "No.", "Do you drink alcohol?"
        )

        self.assertIn("Smoking: Never.", alcohol)
        self.assertIn("Alcohol: No.", alcohol)

    def retired_short_reply_closes_only_the_asked_habit(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components

        for reply in ("no", "nah", "nope"):
            with self.subTest(reply=reply):
                value = merge_lifestyle_answer("", reply, "Do you drink alcohol?")
                self.assertIn(f"Alcohol: {reply}", value)
                self.assertNotIn("Smoking:", value)
                self.assertNotIn(
                    "Current Lifestyle — Alcohol",
                    missing_lifestyle_components(value),
                )

        both = merge_lifestyle_answer(
            "",
            "I do not drink or smoke",
            "Do you drink alcohol?",
            model_lifestyle="Smoking: does not smoke",
        )
        self.assertIn("Alcohol: I do not drink or smoke", both)
        self.assertIn("Smoking: does not smoke", both)

    def retired_batched_reply_stores_only_the_habit_denial(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components
        from src.graph.pure_functions.question_plan import REQUIRED_FIELD_QUESTIONS

        value = merge_lifestyle_answer(
            "",
            "resting, i do zumba, no i dont, no report, relief from pain",
            REQUIRED_FIELD_QUESTIONS["Current Lifestyle"],
        )
        self.assertEqual(value, "")
        self.assertIn(
            "Current Lifestyle — Smoking",
            missing_lifestyle_components(value),
        )

    def test_activity_clause_follows_the_asked_question(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components

        value = merge_lifestyle_answer(
            "",
            "resting, manager, pilates, nope",
            "Could you also tell me:\n"
            "- What makes it feel better or gives relief?\n"
            "- What is your work or usual occupation?\n"
            "- What is your usual activity or exercise routine?\n"
            "- Do you have any related X-rays, MRI, CT scans, or blood reports?",
        )
        self.assertIn("Activity/exercise: pilates", value)
        self.assertIn("Work: manager", value)
        self.assertNotIn("resting", value.lower())
        self.assertNotIn("nope", value.lower())
        self.assertNotIn(
            "Current Lifestyle — Activity or Exercise",
            missing_lifestyle_components(value),
        )

        alone = merge_lifestyle_answer(
            "",
            "pilates",
            "What is your usual activity or exercise routine?",
        )
        self.assertIn("Activity/exercise: pilates", alone)

    def retired_combined_question_two_clause_reply_closes_activity_and_habits(self):
        from src.forms.loader import load_form
        from src.graph.pure_functions.form_validation import validate_section
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components
        from src.graph.pure_functions.question_plan import (
            LIFESTYLE_COMPONENT_QUESTIONS,
            REQUIRED_FIELD_QUESTIONS,
            question_for_missing_fields,
        )

        combined = REQUIRED_FIELD_QUESTIONS["Current Lifestyle"]
        cases = (
            ("Calisthenics , no", "Calisthenics", "no"),
            ("Calisthenics, no i dont", "Calisthenics", "no i dont"),
            ("pilates, no", "pilates", "no"),
        )
        definition = load_form("FRM-01")
        for reply, activity, habit in cases:
            with self.subTest(reply=reply):
                value = merge_lifestyle_answer("", reply, combined)
                self.assertIn(f"Activity/exercise: {activity}", value)
                self.assertIn(f"Smoking: {habit}", value)
                self.assertIn(f"Alcohol: {habit}", value)
                self.assertNotIn("Work:", value)
                self.assertEqual(
                    missing_lifestyle_components(value),
                    ["Current Lifestyle — Work"],
                )

                form = definition.empty_form()
                form["History & Diagnostics"]["Systemic Illness and Surgical History"] = "None"
                form["History & Diagnostics"]["Reports"] = "None"
                form["History & Diagnostics"]["Current Lifestyle"] = value
                missing = validate_section(form, "History & Diagnostics")
                self.assertEqual(missing, ["Current Lifestyle — Work"])
                question = question_for_missing_fields(
                    [("History & Diagnostics", field) for field in missing],
                    definition.field_labels,
                )
                self.assertEqual(
                    question,
                    LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Work"],
                )
                self.assertNotEqual(question, combined)

                with_work = merge_lifestyle_answer(value, "manager", question)
                self.assertEqual(missing_lifestyle_components(with_work), [])

    def retired_combined_question_single_clause_reply_closes_its_part(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components
        from src.graph.pure_functions.question_plan import REQUIRED_FIELD_QUESTIONS

        combined = REQUIRED_FIELD_QUESTIONS["Current Lifestyle"]
        denial = merge_lifestyle_answer("", "no i dont", combined)
        self.assertIn("Smoking: no i dont", denial)
        self.assertIn("Alcohol: no i dont", denial)
        self.assertEqual(
            missing_lifestyle_components(denial),
            ["Current Lifestyle — Work", "Current Lifestyle — Activity or Exercise"],
        )

        activity = merge_lifestyle_answer("", "pilates", combined)
        self.assertEqual(activity, "Activity/exercise: pilates")

        three = merge_lifestyle_answer("", "manager, pilates, no", combined)
        self.assertIn("Work: manager", three)
        self.assertIn("Activity/exercise: pilates", three)
        self.assertIn("Smoking: no", three)
        self.assertIn("Alcohol: no", three)

    def retired_drink_answer_leaves_smoking_to_be_asked(self):
        from src.graph.pure_functions.lifestyle import missing_lifestyle_components
        from src.graph.pure_functions.question_plan import (
            LIFESTYLE_COMPONENT_QUESTIONS,
            REQUIRED_FIELD_QUESTIONS,
            question_for_missing_fields,
        )

        combined = REQUIRED_FIELD_QUESTIONS["Current Lifestyle"]
        value = merge_lifestyle_answer(
            "Work: artist",
            "cardio, yes i drink",
            combined,
        )
        self.assertIn("Activity/exercise: cardio", value)
        self.assertIn("Alcohol: yes i drink", value)
        self.assertIn("Work: artist", value)
        self.assertNotIn("Smoking:", value)
        self.assertEqual(
            missing_lifestyle_components(value),
            ["Current Lifestyle — Smoking"],
        )
        question = question_for_missing_fields(
            [("History & Diagnostics", "Current Lifestyle — Smoking")],
            {},
        )
        self.assertEqual(
            question,
            LIFESTYLE_COMPONENT_QUESTIONS["Current Lifestyle — Smoking"],
        )

        spaced = merge_lifestyle_answer("", "cardio no i dont drink", combined)
        self.assertIn("Activity/exercise: cardio", spaced)
        self.assertIn("Alcohol: no i dont drink", spaced)
        self.assertNotIn("Smoking:", spaced)
        self.assertIn(
            "Current Lifestyle — Smoking",
            missing_lifestyle_components(spaced),
        )

        comma_drink = merge_lifestyle_answer("", "cardio, i dont drink", combined)
        self.assertIn("Activity/exercise: cardio", comma_drink)
        self.assertIn("Alcohol: i dont drink", comma_drink)
        self.assertNotIn("Smoking:", comma_drink)

        lined = merge_lifestyle_answer(
            "",
            "cardio\nno i dont drink or smoke",
            combined,
        )
        self.assertIn("Activity/exercise: cardio", lined)
        self.assertIn("Smoking: no i dont drink or smoke", lined)
        self.assertIn("Alcohol: no i dont drink or smoke", lined)
        self.assertNotIn(
            "Current Lifestyle — Smoking",
            missing_lifestyle_components(lined),
        )
        self.assertNotIn(
            "Current Lifestyle — Alcohol",
            missing_lifestyle_components(lined),
        )

        named = merge_lifestyle_answer(
            "", "yes i drink", "Do you smoke or drink alcohol?"
        )
        self.assertEqual(named, "Alcohol: yes i drink")
        self.assertEqual(
            missing_lifestyle_components(named),
            [
                "Current Lifestyle — Work",
                "Current Lifestyle — Activity or Exercise",
                "Current Lifestyle — Smoking",
            ],
        )

    def test_combined_occupation_and_exercise_do_not_contaminate_each_other(self):
        value = merge_lifestyle_answer(
            "",
            "I work as a firmware engineer, go to the gym four times a week",
            "Could you also tell me:\n"
            "- What is your work or usual occupation?\n"
            "- What is your usual activity or exercise routine?",
        )

        self.assertIn("Work: I work as a firmware engineer", value)
        self.assertIn(
            "Activity/exercise: go to the gym four times a week", value
        )
        work, activity = value.split(";", 1)
        self.assertNotIn("gym", work.lower())
        self.assertNotIn("firmware", activity.lower())

        unsupported = merge_lifestyle_answer(
            "",
            "I am a doctor, unrelated answer",
            "Could you also tell me:\n"
            "- What is your work or usual occupation?\n"
            "- Unsupported historical question?",
        )
        self.assertEqual(unsupported, "Work: I am a doctor")

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

    def test_report_upload_deferral_is_recorded_and_does_not_reprompt(self):
        from src.graph.pure_functions.clarification import build_out_of_flow_response

        question = "Do you have any related X-rays, MRI, CT scans, or blood reports?"
        self.assertIsNone(build_out_of_flow_response("Will share later.", question))

        def unexpected_llm(_prompt):
            self.fail("Contextual report deferral must not call an AI provider")

        self.assertEqual(
            classify_reports_intent("Will share later.", unexpected_llm, question),
            {
                "has_reports": True,
                "wants_upload": False,
                "deferred_upload": True,
            },
        )

        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="interviewing",
            current_section="History & Diagnostics",
            visit_context="specific_complaint",
            user_input="Will share later.",
            history=[{"role": "agent", "message": question}],
        )

        result = run_extract(state, mapping_llm(
            answers=[{
                "field_id": "History & Diagnostics.Reports",
                "status": "answered",
                "value": "Has reports; will share them later",
            }],
            reports={"has_reports": True, "wants_upload": False},
        ))

        reports = result["form"]["History & Diagnostics"]["Reports"]
        self.assertEqual(reports, "Has reports; will share them later")
        self.assertEqual(result["reports_intent"], {"has_reports": True, "wants_upload": False})

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

    def test_extract_persists_decline_and_maps_activity(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        state["current_section"] = "History & Diagnostics"
        state["history"] = [
            {
                "role": "agent",
                "message": "What exercise do you do, and do you have X-rays?",
            }
        ]
        state["user_input"] = (
            "I only walk. I have the X-rays and will share them directly with "
            "the doctor; I don't want to upload here."
        )

        result = run_extract(state, mapping_llm(
            answers=[{
                "field_id": "History & Diagnostics.Current Lifestyle — Activity or Exercise",
                "status": "answered",
                "value": "Walking",
            }, {
                "field_id": "History & Diagnostics.Reports",
                "status": "answered",
                "value": "Has X-rays; will share them directly with the doctor",
            }],
            reports={"has_reports": True, "wants_upload": False},
        ))

        self.assertEqual(
            result["reports_intent"],
            {"has_reports": True, "wants_upload": False},
        )
        self.assertEqual(
            result["form"]["History & Diagnostics"]["Reports"],
            "Has X-rays; will share them directly with the doctor",
        )
        self.assertEqual(
            result["form"]["History & Diagnostics"]["Current Lifestyle"],
            "Activity/exercise: Walking",
        )

    def retired_compact_lifestyle_answer_fills_every_subtopic(self):
        from src.graph.pure_functions.lifestyle import (
            format_lifestyle_value,
            missing_lifestyle_components,
        )
        from src.graph.pure_functions.question_plan import REQUIRED_FIELD_QUESTIONS

        value = merge_lifestyle_answer(
            "",
            "4 times a week, work in company, yes smoke, yes drink",
            REQUIRED_FIELD_QUESTIONS["Current Lifestyle"],
        )

        self.assertEqual(value, "")
        self.assertEqual(
            missing_lifestyle_components(""),
            [
                "Current Lifestyle — Work",
                "Current Lifestyle — Activity or Exercise",
                "Current Lifestyle — Smoking",
                "Current Lifestyle — Alcohol",
            ],
        )
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

        self.assertEqual(value, existing)
        self.assertEqual(value.count("Alcohol:"), 1)

    def retired_compound_work_and_alcohol_answer_is_split_into_correct_fields(self):
        value = merge_lifestyle_answer(
            "",
            "I work in a company, no",
            "Could you also tell me:\n"
            "- What is your work or usual occupation?\n"
            "- Do you drink alcohol?",
        )

        self.assertIn("Work: I work in a company", value)
        self.assertIn("Alcohol: no", value)
        self.assertNotIn("company", value.split("Alcohol:", 1)[-1].lower())
        self.assertEqual(value.count("Work:"), 1)
        self.assertEqual(value.count("Alcohol:"), 1)

        unmatched = merge_lifestyle_answer(
            "",
            "I work in a company no drink",
            "What is your occupation, and do you drink alcohol?",
        )
        self.assertEqual(unmatched, "")

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

    def retired_unpunctuated_exercise_and_smoking_are_separate_components(self):
        value = merge_lifestyle_answer(
            "",
            "I exercise three days in a week, I smoke",
            "Could you also tell me:\n"
            "- What is your usual activity or exercise routine?\n"
            "- Do you smoke or use tobacco?",
        )

        self.assertIn(
            "Activity/exercise: I exercise three days in a week",
            value,
        )
        self.assertIn("Smoking: I smoke", value)
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

        self.assertIn("Work: noter contions", value)
        self.assertIn(
            "Activity/exercise: I exercise three days in a week I smoke",
            value,
        )
        self.assertIn("Smoking: Smokes", value)
        self.assertNotIn(
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

        self.assertEqual(value, polluted)

    def test_health_denial_is_saved_to_health_history_not_occupation(self):
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
                    "- What is your work or usual occupation?"
                ),
            }],
        )

        result = run_extract(state, mapping_llm(answers=[{
            "field_id": "History & Diagnostics.Systemic Illness and Surgical History",
            "status": "denied",
            "value": "No other health conditions",
        }]))

        diagnostics = result["form"]["History & Diagnostics"]
        self.assertEqual(
            diagnostics["Systemic Illness and Surgical History"], "No other health conditions"
        )
        self.assertEqual(diagnostics["Current Lifestyle"], "")

    def test_mapped_occupation_is_saved_as_work(self):
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

        result = run_extract(state, mapping_llm(answers=[{
            "field_id": "History & Diagnostics.Current Lifestyle — Work",
            "status": "answered",
            "value": "Accountant",
        }]))

        lifestyle = result["form"]["History & Diagnostics"]["Current Lifestyle"]
        self.assertEqual(lifestyle, "Work: Accountant")
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

    def test_pain_factor_answer_leaves_lifestyle_unchanged(self):
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
        existing = "Work: Company; Activity/exercise: Yoga"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = existing

        result = run_extract(state, mapping_llm(answers=[{
            "field_id": "Pain Assessment.Aggravating Factors",
            "status": "answered",
            "value": "Walking",
        }, {
            "field_id": "Pain Assessment.Relieving Factors",
            "status": "answered",
            "value": "Massage",
        }]))

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
        self.assertIn("Alcohol: Does not drink alcohol", lifestyle)
        self.assertNotIn("Alcohol: Drinks alcohol", lifestyle)


if __name__ == "__main__":
    unittest.main()
