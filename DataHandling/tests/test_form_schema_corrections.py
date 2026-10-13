import json
import unittest

from src.graph.nodes.correction import make_apply_correction_node
from src.graph.pure_functions.form_extraction import (
    apply_form_correction,
    detect_form_correction,
)
from src.graph.pure_functions.form_schema import FormTarget, resolve_form_target
from src.graph.state import get_fresh_interview_state


class FormSchemaCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
        self.form["Additional Complaint 1"] = {
            "Primary Complaint": "Hand pain",
            "Duration of the Issue": "Two days",
            "Onset (Gradual or Sudden)": "Sudden",
            "Mechanism of Injury or Cause": "Unknown",
            "Severity (1-10)": "4/10",
            "Aggravating Factors": "Gripping",
            "Relieving Factors": "Rest",
        }

    def test_schema_resolves_exact_alias_virtual_and_fuzzy_targets(self):
        cases = (
            (
                "Pain Assessment",
                "pain rating",
                FormTarget("Pain Assessment", "Severity (1-10)"),
            ),
            (
                "Present Complaint",
                "how long",
                FormTarget("Present Complaint", "Duration of the Issue"),
            ),
            (
                "Additional Complaint 1",
                "pain rating",
                FormTarget("Additional Complaint 1", "Severity (1-10)"),
            ),
            (
                "Current Lifestyle",
                "occupation",
                FormTarget("History & Diagnostics", "Current Lifestyle", "work"),
            ),
            (
                "History & Diagnostics",
                "alcohol use",
                FormTarget("History & Diagnostics", "Current Lifestyle", "alcohol"),
            ),
            (
                "Treatment Goals",
                "Specific Expectations from Treatmnt",
                FormTarget("Treatment Goals", "Specific Expectations from Treatment"),
            ),
        )
        for section, field, expected in cases:
            with self.subTest(section=section, field=field):
                self.assertEqual(
                    resolve_form_target(self.form, section, field), expected
                )

    def test_every_live_form_field_resolves_from_schema_identity(self):
        for section, fields in self.form.items():
            if not isinstance(fields, dict):
                continue
            for field in fields:
                with self.subTest(section=section, field=field):
                    self.assertEqual(
                        resolve_form_target(
                            self.form,
                            section.swapcase(),
                            field.swapcase(),
                        ),
                        FormTarget(section, field),
                    )

    def test_unknown_or_invented_target_is_rejected(self):
        self.assertIsNone(
            resolve_form_target(
                self.form,
                "Imaginary Clinical Section",
                "Patient Happiness Number",
            )
        )

    def test_ambiguous_duplicate_field_is_never_guessed(self):
        self.assertIsNone(resolve_form_target(self.form, "", "Primary Complaint"))

    def test_clear_operation_uses_schema_and_preserves_other_fields(self):
        original = {
            "Present Complaint": {
                "Primary Complaint": "Knee pain",
                "Duration of the Issue": "Two weeks",
            }
        }
        updated, success, _ = apply_form_correction(
            {
                "operation": "clear",
                "section_name": "Present Complaint",
                "field_name": "how long",
                "new_value": "",
            },
            original,
            lambda _: "unused",
        )
        self.assertTrue(success)
        self.assertEqual(updated["Present Complaint"]["Duration of the Issue"], "")
        self.assertEqual(updated["Present Complaint"]["Primary Complaint"], "Knee pain")

    def test_one_free_text_message_can_update_multiple_schema_fields(self):
        self.form["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: Designer; Alcohol: Does not drink alcohol"
        )
        self.form["Treatment Goals"]["Long-Term Goals (after 3 months)"] = (
            "Walk comfortably"
        )
        model_result = {
            "is_correction": True,
            "confidence": "high",
            "updates": [
                {
                    "operation": "update",
                    "section_name": "Current Lifestyle",
                    "field_name": "Alcohol",
                    "new_value": "Drinks alcohol occasionally",
                },
                {
                    "operation": "update",
                    "section_name": "Treatment Goals",
                    "field_name": "long term goal",
                    "new_value": "Return to swimming",
                },
            ],
            "needs_clarification": False,
            "clarification_question": "",
        }

        correction = detect_form_correction(
            "Actually I drink occasionally, and my long-term goal is swimming again",
            self.form,
            is_summary_mode=True,
            llm_complete=lambda _prompt: json.dumps(model_result),
        )
        state = {
            "form": self.form,
            "history": [{"role": "user", "message": "correction"}],
            "correction_data": correction,
            "phase": "summary",
        }
        result = make_apply_correction_node(lambda _: "unused")(state)

        self.assertTrue(result["correction_applied"])
        self.assertIn(
            "Alcohol: Drinks alcohol",
            result["form"]["History & Diagnostics"]["Current Lifestyle"],
        )
        self.assertEqual(
            result["form"]["Treatment Goals"]["Long-Term Goals (after 3 months)"],
            "Return to swimming",
        )


if __name__ == "__main__":
    unittest.main()
