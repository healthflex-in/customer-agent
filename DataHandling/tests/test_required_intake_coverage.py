import unittest
from pathlib import Path

from src.forms.loader import load_form
from src.graph.pure_functions.question_plan import (
    REQUIRED_FIELD_QUESTIONS,
    question_for_missing_fields,
)
from src.graph.pure_functions.form_validation import (
    get_all_missing_fields,
    validate_section,
)


class RequiredIntakeCoverageTests(unittest.TestCase):
    def test_resume_anchors_question_in_saved_complaint_without_reasking_it(self):
        from src.graph.pure_functions.question_plan import contextual_resume_question
        form = {"Present Complaint": {"Primary Complaint": "Right knee pain", "Onset (Gradual or Sudden)": "Sudden"}}
        missing = [("Present Complaint", "Duration of the Issue")]
        response = contextual_resume_question(form, missing, {})
        self.assertIn("Welcome back", response)
        self.assertIn("Right knee pain", response)
        self.assertIn("How long", response)
        self.assertNotIn("Did it start", response)
        self.assertNotIn("Is this information correct", response)

    def setUp(self):
        self.definition = load_form("FRM-01")

    def test_every_required_non_referral_field_has_deterministic_question(self):
        expected = {
            field
            for section, fields in self.definition.sections.items()
            if section != "Referral"
            for field in fields
            if "(If Any)" not in field and "(Optional)" not in field
        }
        self.assertEqual(set(REQUIRED_FIELD_QUESTIONS), expected)

    def test_question_batches_include_each_missing_field(self):
        missing = [
            ("Present Complaint", "Mechanism of Injury or Cause"),
            ("Treatment Goals", "Short-Term Goals (within 3 months)"),
            ("Treatment Goals", "Long-Term Goals (after 3 months)"),
            ("Treatment Goals", "Specific Expectations from Treatment"),
        ]
        question = question_for_missing_fields(missing, self.definition.field_labels)

        for _, field in missing:
            self.assertIn(REQUIRED_FIELD_QUESTIONS[field], question)

    def test_previous_consultation_requires_status_after_treatment(self):
        section = {
            "Previous Diagnosis or Advice and Prescribed Treatment Taken": (
                "Saw a physiotherapist and was prescribed exercises"
            ),
            "Current Status of Issue (Improved, Same, Worse)": "",
        }
        self.assertEqual(
            validate_section({"Previous Consultations": section}, "Previous Consultations"),
            ["Current Status of Issue (Improved, Same, Worse)"],
        )

    def test_blank_answers_stay_blank_and_block_completion(self):
        form = self.definition.empty_form()
        unchanged = {section: dict(fields) for section, fields in form.items()}

        self.assertEqual(unchanged, form)
        self.assertTrue(get_all_missing_fields(unchanged, list(self.definition.sections)))
        self.assertNotIn("Not mentioned by patient", str(unchanged))

        generate_source = (
            Path(__file__).resolve().parents[1] / "src" / "graph" / "nodes" / "generate.py"
        ).read_text(encoding="utf-8")
        fill_start = generate_source.index("def _fill_unanswered_fields")
        fill_end = generate_source.index("def make_generate_question_node", fill_start)
        self.assertNotIn('filled[section][field] = "Not mentioned by patient"', generate_source[fill_start:fill_end])

    def test_empty_persisted_draft_has_same_missing_fields_as_blank_template(self):
        sections = list(self.definition.sections)
        self.assertEqual(
            get_all_missing_fields({}, sections),
            get_all_missing_fields(self.definition.empty_form(), sections),
        )

    def test_partial_section_does_not_hide_absent_required_fields(self):
        form = {"Present Complaint": {"Primary Complaint": "Right knee pain"}}
        self.assertEqual(
            set(validate_section(form, "Present Complaint")),
            set(self.definition.sections["Present Complaint"]) - {"Primary Complaint"},
        )

    def test_explicit_no_previous_consultation_does_not_require_status(self):
        section = {
            "Previous Diagnosis or Advice and Prescribed Treatment Taken": (
                "None — no previous consultations"
            ),
            "Current Status of Issue (Improved, Same, Worse)": "",
        }
        self.assertEqual(
            validate_section({"Previous Consultations": section}, "Previous Consultations"),
            [],
        )

    def test_summary_and_confirmation_have_the_same_required_field_gate(self):
        backend_root = Path(__file__).resolve().parents[1]
        generate_source = (backend_root / "src" / "graph" / "nodes" / "generate.py").read_text(
            encoding="utf-8"
        )
        summary_source = (backend_root / "src" / "graph" / "nodes" / "summary.py").read_text(
            encoding="utf-8"
        )

        self.assertGreaterEqual(
            generate_source.count("required_response_before_summary(state, history)"), 2
        )
        self.assertIn("required_response_before_summary(", summary_source)


if __name__ == "__main__":
    unittest.main()
