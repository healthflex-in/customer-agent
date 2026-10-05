import copy
import unittest

from src.graph.pure_functions.clinical_value_guard import (
    guard_new_pain_factor_evidence,
    sanitize_extracted_form,
)
from src.graph.pure_functions.reasoning_extractor import _build_conversation_text
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


if __name__ == "__main__":
    unittest.main()
