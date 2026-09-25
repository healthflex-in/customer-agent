import unittest

from app.forms.progress import calculate_form_progress, calculate_section_completion_status


class AdditionalComplaintProgressTests(unittest.TestCase):
    def test_incomplete_additional_complaint_prevents_100_percent(self):
        form = {
            "Present Complaint": {"Primary Complaint": "Head pain"},
            "Additional Complaint 1": {
                "Primary Complaint": "Leg pain", "Severity (1-10)": "",
            },
        }
        self.assertLess(calculate_form_progress(form), 100)
        status = calculate_section_completion_status(form)
        additional = next(step for step in status["steps"] if step["name"] == "Additional Complaint 1")
        self.assertFalse(additional["isComplete"])

    def test_completed_additional_complaint_can_reach_100_percent(self):
        form = {
            "Present Complaint": {"Primary Complaint": "Head pain"},
            "Additional Complaint 1": {
                "Primary Complaint": "Leg pain", "Severity (1-10)": "5/10",
            },
        }
        self.assertEqual(calculate_form_progress(form), 100)
