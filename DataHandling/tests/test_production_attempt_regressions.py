import unittest

from src.forms.loader import load_form
from src.graph.pure_functions.additional_complaint import (
    capture_additional_complaint,
    is_additional_complaint_cancellation,
    split_update_and_addition,
    validated_additional_complaint,
)
from src.graph.pure_functions.complaint_severity import explicit_severity_updates
from src.graph.pure_functions.resume import build_resume_plan
from src.graph.pure_functions.turn_tracking import update_loop_counters


def completed_form():
    form = load_form("FRM-01").empty_form()
    for section, fields in form.items():
        for field in fields:
            fields[field] = "Provided"
    form["Previous Consultations"][
        "Previous Diagnosis or Advice and Prescribed Treatment Taken"
    ] = "No previous consultations"
    form["Previous Consultations"][
        "Current Status of Issue (Improved, Same, Worse)"
    ] = "Not applicable"
    form["History & Diagnostics"]["Current Lifestyle"] = (
        "Work: office; Activity/exercise: walking; Smoking: does not smoke; "
        "Alcohol: does not drink alcohol"
    )
    return form


class ProductionAttemptRegressionTests(unittest.TestCase):
    def test_case_1_resume_restores_interviewing_not_welcome(self):
        form = completed_form()
        form["Pain Assessment"]["Relieving Factors"] = ""

        plan = build_resume_plan(form, "Pain Assessment")

        self.assertEqual(plan.phase, "interviewing")
        self.assertEqual(plan.current_section, "Pain Assessment")
        self.assertIn(
            ("Pain Assessment", "Relieving Factors"), plan.missing
        )
        self.assertIn("What makes it feel better", plan.message)

    def test_resume_does_not_repeat_completed_lifestyle_components(self):
        form = completed_form()
        form["Treatment Goals"]["Specific Expectations from Treatment"] = ""

        plan = build_resume_plan(form, "Treatment Goals")

        self.assertEqual(plan.phase, "interviewing")
        self.assertNotIn("occupation", plan.message.lower())
        self.assertNotIn("smoke", plan.message.lower())
        self.assertNotIn("alcohol", plan.message.lower())
        self.assertIn("expectations from treatment", plan.message.lower())

    def test_case_5_all_required_values_resume_to_summary(self):
        plan = build_resume_plan(completed_form(), "Treatment Goals")

        self.assertEqual(plan.phase, "summary")
        self.assertEqual(plan.question_round, 3)
        self.assertEqual(plan.missing, [])
        self.assertIn("Here's a summary", plan.message)
        self.assertIn("If it is accurate, reply looks good", plan.message)
        self.assertNotIn("ask your clinician", plan.message)

    def test_treatment_expectation_is_not_an_additional_complaint(self):
        text = (
            "I also expect that the treatment won't just be rest or pain pills. "
            "I am looking for a plan to heal without surgery."
        )
        self.assertIsNone(validated_additional_complaint(text))

    def test_genuine_si_joint_complaint_is_accepted_concisely(self):
        value = validated_additional_complaint(
            "I've also been experiencing SI-joint pain since June 2026."
        )
        self.assertEqual(value, "si-joint pain")

    def test_case_3_combined_correction_and_new_complaint(self):
        form = completed_form()
        form["Present Complaint"]["Primary Complaint"] = "Leg pain"
        form["Pain Assessment"]["Primary Location of Pain"] = "Leg"
        form["Pain Assessment"]["Severity (1-10)"] = "9/10"
        text = "please increase the leg pain rating to 10 and I also have hand pain"

        correction, addition = split_update_and_addition(text)
        updates = explicit_severity_updates(
            form,
            correction,
            "Pain Assessment",
            "On a scale of 0 to 10, how severe is the pain?",
        )
        updated, section = capture_additional_complaint(form, addition)

        self.assertEqual(
            updates[("Pain Assessment", "Severity (1-10)")], "10/10"
        )
        self.assertEqual(section, "Additional Complaint 1")
        self.assertEqual(updated[section]["Primary Complaint"], "hand pain")

    def test_case_3_decline_variants_cancel_active_additional_complaint(self):
        variants = [
            "no additional complaint",
            "no other complaint",
            "lets skip this",
            "that's all",
            "no need enough bye",
            "there is no addiotnal complaint",
            "theere is o additional complaint",
        ]
        for text in variants:
            with self.subTest(text=text):
                self.assertTrue(is_additional_complaint_cancellation(text))

    def test_repeated_question_is_counted_for_turn_telemetry(self):
        state = {}
        counts = [
            update_loop_counters(
                state,
                missing_ids=["Additional Complaint 1.Relieving Factors"],
                question_ids=["Additional Complaint 1.Relieving Factors"],
            )
            for _ in range(3)
        ]

        self.assertEqual(counts, [(1, 1), (2, 2), (3, 3)])

if __name__ == "__main__":
    unittest.main()
