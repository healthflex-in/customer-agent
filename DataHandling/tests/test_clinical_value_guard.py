import copy
import unittest

from src.graph.pure_functions.clinical_value_guard import sanitize_extracted_form
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


if __name__ == "__main__":
    unittest.main()
