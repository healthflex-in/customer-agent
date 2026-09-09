import unittest
from unittest.mock import MagicMock

from app.clinical.escalation import (
    POLICY_VERSION,
    assess_urgent_risk,
    ensure_escalation_indexes,
    record_escalation,
)


class ClinicalEscalationTests(unittest.TestCase):
    def test_explicit_self_harm_language_escalates(self):
        for text in (
            "I want to kill myself",
            "I feel suicidal",
            "I think I would be better off dead",
            "I don't want to live anymore",
        ):
            with self.subTest(text=text):
                decision = assess_urgent_risk(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, "self_harm")
                self.assertTrue(decision.stop_interview)

    def test_clear_negation_does_not_escalate(self):
        for text in (
            "I am not suicidal",
            "I don't want to die",
            "I have no self-harm thoughts",
            "I wouldn't hurt myself",
        ):
            with self.subTest(text=text):
                self.assertIsNone(assess_urgent_risk(text))

    def test_positive_structured_phq9_item9_escalates(self):
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_phq9_interest", "prom_phq9_item_9"],
            "questions": [
                "Little interest or pleasure in doing things?",
                "Thoughts that you would be better off dead, or of hurting yourself?",
            ],
        }
        decision = assess_urgent_risk(
            "Not at all | Several days",
            question_meta=meta,
            structured_answers=["Not at all", "Several days"],
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule_id, "PHQ9_ITEM9_POSITIVE")

    def test_not_at_all_phq9_item9_does_not_escalate(self):
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_phq9_q9"],
            "questions": ["Thoughts that you would be better off dead?"],
        }
        self.assertIsNone(
            assess_urgent_risk(
                "Not at all",
                question_meta=meta,
                structured_answers=["Not at all"],
            )
        )

    def test_unrelated_phq9_frequency_does_not_escalate(self):
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_phq9_interest"],
            "questions": ["Little interest or pleasure in doing things?"],
        }
        self.assertIsNone(
            assess_urgent_risk(
                "Nearly every day",
                question_meta=meta,
                structured_answers=["Nearly every day"],
            )
        )

    def test_single_batched_phq9_item9_escalates_without_pipe_payload(self):
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_phq9_thoughts"],
            "questions": ["Thoughts that you would be better off dead?"],
        }
        decision = assess_urgent_risk("Several days", question_meta=meta)
        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule_id, "PHQ9_ITEM9_POSITIVE")

    def test_negation_does_not_mask_later_explicit_risk(self):
        decision = assess_urgent_risk(
            "I am not suicidal, but I do want to kill myself now"
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule_id, "EXPLICIT_SELF_HARM_LANGUAGE")

    def test_cauda_equina_requires_compound_signal(self):
        self.assertIsNone(assess_urgent_risk("I have bladder problems"))
        self.assertIsNone(assess_urgent_risk("My inner thighs feel numb"))
        decision = assess_urgent_risk(
            "I suddenly lost bladder control and have numbness between my legs"
        )
        self.assertIsNotNone(decision)
        self.assertEqual(decision.rule_id, "CAUDA_EQUINA_COMBINATION")

    def test_audit_event_omits_triggering_text(self):
        collection = MagicMock()
        collection.insert_one.return_value.inserted_id = "event-1"
        decision = assess_urgent_risk("I want to kill myself")

        event_id = record_escalation(
            collection,
            decision,
            patient_id="patient-1",
            form_id="FRM-01",
            attempt_id="attempt-1",
            session_id="session-1",
            request_id="request-1",
        )

        self.assertEqual(event_id, "event-1")
        event = collection.insert_one.call_args.args[0]
        self.assertEqual(event["policyVersion"], POLICY_VERSION)
        self.assertEqual(event["status"], "detected")
        self.assertNotIn("text", event)
        self.assertNotIn("answer", event)

    def test_indexes_support_patient_history_and_open_work_queue(self):
        collection = MagicMock()
        ensure_escalation_indexes(collection)
        self.assertEqual(collection.create_index.call_count, 2)


if __name__ == "__main__":
    unittest.main()
