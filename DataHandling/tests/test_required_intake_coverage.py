import unittest
from pathlib import Path

from src.forms.loader import load_form
from src.graph.pure_functions.form_validation import (
    get_all_missing_fields,
    validate_section,
)
from src.graph.pure_functions.question_plan import (
    LIFESTYLE_COMPONENT_QUESTIONS,
    REQUIRED_FIELD_QUESTIONS,
    contextual_resume_question,
    question_for_missing_fields,
)


_PROHIBITED_QUESTION_TERMS = ("smok", "tobacco", "drink", "alcohol")


class RequiredIntakeCoverageTests(unittest.TestCase):
    def setUp(self):
        self.definition = load_form("FRM-01")

    def assertNoRetiredLifestyleQuestion(self, text):
        lowered = text.lower()
        for term in _PROHIBITED_QUESTION_TERMS:
            self.assertNotIn(term, lowered)

    def test_resume_anchors_question_in_saved_complaint_without_reasking_it(self):
        form = {
            "Present Complaint": {
                "Primary Complaint": "Right knee pain",
                "Onset (Gradual or Sudden)": "Sudden",
            }
        }
        response = contextual_resume_question(
            form, [("Present Complaint", "Duration of the Issue")], {}
        )
        self.assertIn("Welcome back", response)
        self.assertIn("Right knee pain", response)
        self.assertIn("How long", response)
        self.assertNotIn("Did it start", response)
        self.assertNoRetiredLifestyleQuestion(response)

    def test_blank_lifestyle_yields_exactly_work_and_activity_gaps(self):
        form = self.definition.empty_form()
        form["History & Diagnostics"]["Systemic Illness and Surgical History"] = "None"
        form["History & Diagnostics"]["Reports"] = "None"

        missing = validate_section(form, "History & Diagnostics")

        self.assertEqual(
            missing,
            [
                "Current Lifestyle — Work",
                "Current Lifestyle — Activity or Exercise",
            ],
        )
        question = question_for_missing_fields(
            [("History & Diagnostics", field) for field in missing],
            self.definition.field_labels,
        )
        self.assertIn(LIFESTYLE_COMPONENT_QUESTIONS[missing[0]], question)
        self.assertIn(LIFESTYLE_COMPONENT_QUESTIONS[missing[1]], question)
        self.assertNoRetiredLifestyleQuestion(question)

    def test_work_and_activity_complete_lifestyle_despite_unrelated_text(self):
        form = self.definition.empty_form()
        diagnostics = form["History & Diagnostics"]
        diagnostics["Systemic Illness and Surgical History"] = "None"
        diagnostics["Reports"] = "None"
        diagnostics["Current Lifestyle"] = (
            "Historical note: keep exactly; Work: software developer; "
            "Activity/exercise: gym three times per week; Custom label: retained"
        )

        self.assertEqual(validate_section(form, "History & Diagnostics"), [])

    def test_fresh_resume_and_form_templates_never_ask_retired_topics(self):
        rendered = [
            self.definition.general_visit_q1,
            *(question.text for question in self.definition.predefined_questions),
            *REQUIRED_FIELD_QUESTIONS.values(),
            *LIFESTYLE_COMPONENT_QUESTIONS.values(),
            contextual_resume_question(
                {},
                [
                    ("History & Diagnostics", "Current Lifestyle — Work"),
                    ("History & Diagnostics", "Current Lifestyle — Activity or Exercise"),
                ],
                self.definition.field_labels,
            ),
        ]
        for text in rendered:
            self.assertNoRetiredLifestyleQuestion(text)

        root = Path(__file__).resolve().parents[1]
        for relative in ("src/prompts.py", "src/llm/functionalities.py"):
            source = (root / relative).read_text(encoding="utf-8").lower()
            for phrase in (
                "do you smoke",
                "smoke or drink",
                "drink alcohol",
                "smoking and alcohol habits",
            ):
                self.assertNotIn(phrase, source)

    def test_every_required_field_has_deterministic_question(self):
        expected = {
            field
            for fields in self.definition.sections.values()
            for field in fields
            if "(If Any)" not in field and "(Optional)" not in field
        }
        self.assertEqual(set(REQUIRED_FIELD_QUESTIONS), expected)
        self.assertNotIn("Referral", self.definition.sections)
        self.assertFalse(hasattr(self.definition, "referral_question"))

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

    def test_blank_answers_block_completion(self):
        form = self.definition.empty_form()
        self.assertTrue(get_all_missing_fields(form, list(self.definition.sections)))
        self.assertNotIn("Not mentioned by patient", str(form))

    def test_dependent_field_waits_for_its_parent_answer(self):
        parent = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
        status = "Current Status of Issue (Improved, Same, Worse)"
        cases = [
            ({parent: "", status: ""}, [parent]),
            ({parent: "Physio advised rest", status: ""}, [status]),
            ({parent: "Never consulted anyone", status: "Not applicable"}, []),
        ]
        for section, expected in cases:
            with self.subTest(section=section):
                self.assertEqual(
                    validate_section({"Previous Consultations": section}, "Previous Consultations"),
                    expected,
                )

if __name__ == "__main__":
    unittest.main()
