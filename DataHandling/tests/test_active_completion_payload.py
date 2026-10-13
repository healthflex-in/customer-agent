import unittest

from src.graph.server_adapter import build_interview_state_from_graph


class ActiveCompletionPayloadTests(unittest.TestCase):
    def test_completed_phase_marks_final_message_read_only(self):
        result = build_interview_state_from_graph(
            {"form": {}, "current_section": "Referral", "phase": "complete"},
            {"form_id": "FRM-01", "user_id": "u", "attempt_id": "ATT-test"},
            progress_override=100,
        )
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["locked"])
        self.assertEqual(result["attemptId"], "ATT-test")

    def test_filled_progress_does_not_lock_unconfirmed_summary(self):
        result = build_interview_state_from_graph(
            {"form": {}, "current_section": "Referral", "phase": "summary"},
            {"form_id": "FRM-01"}, progress_override=100,
        )
        self.assertFalse(result["locked"])
        self.assertEqual(result["status"], "in_progress")
