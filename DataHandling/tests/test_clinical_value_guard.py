import copy
import unittest

from src.graph.pure_functions.clinical_value_guard import (
    apply_explicit_pain_factor_answers,
    explicit_pain_factor_values,
    guard_new_pain_factor_evidence,
    sanitize_extracted_form,
)
from src.graph.pure_functions.form_validation import get_all_missing_fields
from src.graph.pure_functions.question_plan import question_for_missing_fields
from src.graph.pure_functions.reasoning_extractor import _build_conversation_text
from src.forms.loader import load_form
from src.graph.state import get_fresh_interview_state


class ClinicalValueGuardTests(unittest.TestCase):
    def setUp(self):
        self.form = get_fresh_interview_state("u", "FRM-01", "s")["form"]

    def test_refused_medical_question_is_removed_from_extraction_history(self):
        history = [
            {"role": "agent", "message": "What do you expect from treatment?"},
            {
                "role": "user",
                "message": "can I eat painkiller and start playing again",
                "clinical_extraction": False,
            },
            {
                "role": "agent",
                "message": "I cannot provide medication guidance.",
                "clinical_extraction": False,
            },
        ]

        conversation = _build_conversation_text(history, "I want to become pain free")

        self.assertNotIn("painkiller", conversation)
        self.assertNotIn("medication guidance", conversation)
        self.assertIn("I want to become pain free", conversation)

    def test_old_boundary_history_without_marker_is_also_filtered(self):
        history = [
            {"role": "agent", "message": "What do you expect from treatment?"},
            {"role": "user", "message": "can I eat painkiller and play again"},
            {
                "role": "agent",
                "message": (
                    "I'm here to collect information for your clinical assessment, "
                    "so I'm unable to recommend medication."
                ),
            },
        ]

        conversation = _build_conversation_text(history, "I want less pain")

        self.assertNotIn("painkiller", conversation)
        self.assertIn("I want less pain", conversation)

    def test_medication_question_cannot_become_treatment_expectation(self):
        extracted = copy.deepcopy(self.form)
        extracted["Treatment Goals"]["Specific Expectations from Treatment"] = (
            "Ability to eat painkiller and start playing again"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(
            result["Treatment Goals"]["Specific Expectations from Treatment"], ""
        )

    def test_medication_question_cannot_become_any_treatment_goal(self):
        extracted = copy.deepcopy(self.form)
        extracted["Treatment Goals"]["Long-Term Goals (after 3 months)"] = (
            "Take painkiller and return to playing football"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(
            result["Treatment Goals"]["Long-Term Goals (after 3 months)"], ""
        )

    def test_model_commentary_cannot_become_pain_location(self):
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Primary Location of Pain"] = (
            "Rear (likely referring to a posterior body part, but not specific enough "
            "for a precise location)"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(result["Pain Assessment"]["Primary Location of Pain"], "")

    def test_transcription_noise_cannot_become_duration(self):
        extracted = copy.deepcopy(self.form)
        extracted["Present Complaint"]["Duration of the Issue"] = (
            "Recent/Acute (started suddenly, dealt with tomorrow)"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(result["Present Complaint"]["Duration of the Issue"], "")

    def test_transcription_noise_cannot_move_to_another_complaint_field(self):
        extracted = copy.deepcopy(self.form)
        extracted["Present Complaint"]["Mechanism of Injury or Cause"] = (
            "Rear dealt with tomorrow"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(
            result["Present Complaint"]["Mechanism of Injury or Cause"], ""
        )

    def test_valid_location_duration_and_goal_are_preserved(self):
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Primary Location of Pain"] = "Lower back"
        extracted["Present Complaint"]["Duration of the Issue"] = "Two weeks"
        extracted["Treatment Goals"]["Specific Expectations from Treatment"] = (
            "Return to football without pain"
        )

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(result["Pain Assessment"]["Primary Location of Pain"], "Lower back")
        self.assertEqual(result["Present Complaint"]["Duration of the Issue"], "Two weeks")
        self.assertEqual(
            result["Treatment Goals"]["Specific Expectations from Treatment"],
            "Return to football without pain",
        )

    def test_invalid_new_value_does_not_overwrite_existing_valid_value(self):
        self.form["Pain Assessment"]["Primary Location of Pain"] = "Right knee"
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Primary Location of Pain"] = "Rear"

        result = sanitize_extracted_form(self.form, extracted)

        self.assertEqual(result["Pain Assessment"]["Primary Location of Pain"], "Right knee")

    def test_mechanism_is_not_inferred_as_primary_aggravating_factor(self):
        self.form["Present Complaint"]["Mechanism of Injury or Cause"] = (
            "Playing football"
        )
        self.form["Additional Complaint 1"] = {
            "Primary Complaint": "Hand pain",
            "Duration of the Issue": "",
            "Onset (Gradual or Sudden)": "",
            "Mechanism of Injury or Cause": "",
            "Severity (1-10)": "",
            "Aggravating Factors": "",
            "Relieving Factors": "",
        }
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Aggravating Factors"] = "Playing"
        extracted["Additional Complaint 1"]["Aggravating Factors"] = "Gripping"

        result = guard_new_pain_factor_evidence(
            self.form,
            extracted,
            user_input="Gripping",
            last_question="What makes the hand pain worse?",
            current_section="Additional Complaint 1",
        )

        self.assertEqual(result["Pain Assessment"]["Aggravating Factors"], "")
        self.assertEqual(
            result["Additional Complaint 1"]["Aggravating Factors"], "Gripping"
        )

    def test_direct_answer_to_primary_aggravating_question_is_preserved(self):
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Aggravating Factors"] = "Playing"

        result = guard_new_pain_factor_evidence(
            self.form,
            extracted,
            user_input="Playing",
            last_question="What activities or situations make it worse?",
            current_section="Pain Assessment",
        )

        self.assertEqual(
            result["Pain Assessment"]["Aggravating Factors"], "Playing"
        )

    def test_explicit_unprompted_aggravating_factor_is_preserved(self):
        extracted = copy.deepcopy(self.form)
        extracted["Pain Assessment"]["Aggravating Factors"] = "Walking"

        result = guard_new_pain_factor_evidence(
            self.form,
            extracted,
            user_input="Walking makes my pain worse",
            last_question="How long have you had it?",
            current_section="Present Complaint",
        )

        self.assertEqual(
            result["Pain Assessment"]["Aggravating Factors"], "Walking"
        )

    def test_exact_uat_message_accepts_both_factors_and_does_not_reask(self):
        patient_message = (
            "I have pain in my right knee that started about six weeks ago after I "
            "twisted it while playing football. The pain is around 6 out of 10 and "
            "gets worse when climbing stairs, squatting, or running. Rest and "
            "applying ice make it feel better."
        )
        extracted = copy.deepcopy(self.form)
        extracted["Present Complaint"].update({
            "Primary Complaint": "Right knee pain",
            "Duration of the Issue": "About six weeks",
            "Onset (Gradual or Sudden)": "Sudden",
            "Mechanism of Injury or Cause": "Twisted while playing football",
        })
        extracted["Pain Assessment"].update({
            "Primary Location of Pain": "Right knee",
            "Severity (1-10)": "6/10",
            "Aggravating Factors": "Climbing stairs, squatting, or running",
            "Relieving Factors": "Rest and applying ice",
        })

        guarded = guard_new_pain_factor_evidence(
            self.form,
            extracted,
            user_input=patient_message,
            last_question="What brings you in today?",
            current_section="Present Complaint",
        )

        self.assertEqual(
            guarded["Pain Assessment"]["Aggravating Factors"],
            "Climbing stairs, squatting, or running",
        )
        self.assertEqual(
            guarded["Pain Assessment"]["Relieving Factors"],
            "Rest and applying ice",
        )
        missing = get_all_missing_fields(guarded, list(guarded))
        missing_ids = {
            f"{section}.{field}"
            for section, fields in missing.items()
            for field in fields
        }
        self.assertNotIn("Pain Assessment.Aggravating Factors", missing_ids)
        self.assertNotIn("Pain Assessment.Relieving Factors", missing_ids)

        flat_missing = [
            (section, field)
            for section, fields in missing.items()
            for field in fields
        ]
        next_question = question_for_missing_fields(
            flat_missing,
            load_form("FRM-01").field_labels,
        ).lower()
        self.assertNotIn("what activities, movements, or situations make it worse", next_question)
        self.assertNotIn("what makes it feel better or gives relief", next_question)

    def test_exact_uat_message_deterministically_fills_both_factors(self):
        patient_message = (
            "I have pain in my right knee that started about six weeks ago after I "
            "twisted it while playing football. The pain is around 6 out of 10 and "
            "gets worse when climbing stairs, squatting, or running. Rest and "
            "applying ice make it feel better."
        )

        updated = apply_explicit_pain_factor_answers(
            self.form,
            active_section="Pain Assessment",
            user_input=patient_message,
        )

        self.assertIn(
            "gets worse when",
            updated["Pain Assessment"]["Aggravating Factors"],
        )
        self.assertIn(
            "make it feel better",
            updated["Pain Assessment"]["Relieving Factors"],
        )

    def test_supported_relationship_phrasings_require_explicit_evidence(self):
        aggravating = (
            "It gets worse when climbing stairs",
            "It is worse with running",
            "Squatting makes it worse",
            "The pain increases when walking",
            "It starts paining when lifting",
            "It hurts while doing push-ups",
        )
        relieving = (
            "Rest makes it feel better",
            "Applying ice makes it better",
            "Lying down gives relief",
            "Standing makes it better",
            "Rest helps",
            "Nothing gives relief",
        )
        for text in aggravating:
            with self.subTest(text=text):
                self.assertIn("Aggravating Factors", explicit_pain_factor_values(text))
        for text in relieving:
            with self.subTest(text=text):
                self.assertIn("Relieving Factors", explicit_pain_factor_values(text))

        self.assertEqual(explicit_pain_factor_values("I played football"), {})
        self.assertEqual(explicit_pain_factor_values("I usually climb stairs"), {})

    def test_contextual_normal_standing_answer_is_relief_not_a_question(self):
        from src.graph.pure_functions.clarification import build_out_of_flow_response

        text = "When I stand normally, I feel fine."
        question = "What makes it feel better or gives relief?"

        self.assertIsNone(build_out_of_flow_response(text, question))
        self.assertEqual(
            explicit_pain_factor_values(text),
            {"Relieving Factors": "When I stand normally, I feel fine"},
        )

        updated = apply_explicit_pain_factor_answers(
            self.form,
            active_section="Pain Assessment",
            user_input=text,
        )
        self.assertEqual(
            updated["Pain Assessment"]["Relieving Factors"],
            "When I stand normally, I feel fine",
        )


if __name__ == "__main__":
    unittest.main()
