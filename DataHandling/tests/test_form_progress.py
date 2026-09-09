import ast
import unittest
from pathlib import Path

from app.forms.progress import (
    CURRENT_STATUS_FIELD,
    PREVIOUS_CONSULTATIONS_FIELD,
    calculate_form_progress,
    calculate_section_completion_status,
)


class FormProgressCharacterizationTests(unittest.TestCase):
    def test_empty_form_preserves_public_response_shape(self):
        self.assertEqual(calculate_form_progress({}), 0)
        self.assertEqual(
            calculate_section_completion_status({}),
            {"totalSteps": 0, "currentStep": 0, "progress": 0, "steps": []},
        )

    def test_no_previous_consultation_completes_single_conditional_field(self):
        form = {
            "Previous Consultations": {
                PREVIOUS_CONSULTATIONS_FIELD: "I have not consulted a doctor",
                CURRENT_STATUS_FIELD: "",
            }
        }
        self.assertEqual(calculate_form_progress(form), 100)
        status = calculate_section_completion_status(form)
        self.assertEqual(status["completedSteps"], 1)
        self.assertEqual(status["steps"][0]["totalFields"], 1)

    def test_status_without_consultation_is_not_a_complete_section(self):
        form = {
            "Previous Consultations": {
                PREVIOUS_CONSULTATIONS_FIELD: "",
                CURRENT_STATUS_FIELD: "Worse",
            }
        }
        self.assertEqual(calculate_form_progress(form), 50)
        status = calculate_section_completion_status(form)
        self.assertEqual(status["completedSteps"], 0)
        self.assertEqual(status["steps"][0]["filledFields"], 0)

    def test_field_progress_keeps_referral_source_validation(self):
        form = {"Referral": {"Source": "yes", "Expectations": "Less pain"}}
        self.assertEqual(calculate_form_progress(form), 50)
        # Section completion historically treats a patient answer of "yes" as
        # non-placeholder text. Preserve this distinction during extraction.
        self.assertEqual(
            calculate_section_completion_status(form)["completedSteps"], 1
        )

    def test_history_none_is_a_valid_patient_answer(self):
        form = {"History & Diagnostics": {"Surgeries": "none"}}
        self.assertEqual(calculate_form_progress(form), 100)
        self.assertEqual(
            calculate_section_completion_status(form)["completedSteps"], 1
        )

    def test_steps_remain_in_canonical_order_after_completion(self):
        form = {
            "Present Complaint": {"Complaint": "knee pain"},
            "Pain Assessment": {"Severity": ""},
            "Referral": {"Source": "Website"},
        }
        status = calculate_section_completion_status(form)
        self.assertEqual(
            [step["name"] for step in status["steps"]],
            ["Present Complaint", "Pain Assessment", "Referral"],
        )
        self.assertEqual(status["completedSteps"], 2)
        self.assertEqual(status["currentStep"], 3)

    def test_unknown_and_non_mapping_sections_do_not_change_progress(self):
        form = {"Unknown": {"Field": "value"}, "Present Complaint": "invalid"}
        self.assertEqual(calculate_form_progress(form), 0)
        status = calculate_section_completion_status(form)
        self.assertEqual(status["totalSteps"], 0)
        self.assertEqual(status["completedSteps"], 0)


class FormProgressArchitectureTests(unittest.TestCase):
    def test_server_imports_progress_functions_instead_of_defining_them(self):
        server_path = Path(__file__).resolve().parents[1] / "server.py"
        tree = ast.parse(server_path.read_text(encoding="utf-8"))
        top_level_definitions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn("calculate_form_progress", top_level_definitions)
        self.assertNotIn("calculate_section_completion_status", top_level_definitions)

        imported_names = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module == "app.forms.progress":
                imported_names.update(alias.name for alias in node.names)
        self.assertEqual(
            imported_names,
            {"calculate_form_progress", "calculate_section_completion_status"},
        )


if __name__ == "__main__":
    unittest.main()
