import unittest
from pathlib import Path

from app.clinical.scope import (
    NON_MSK_INTAKE_MESSAGE,
    assess_msk_intake_scope,
)


class ClinicalScopeTests(unittest.TestCase):
    def test_clear_non_msk_fever_and_cold_is_redirected(self):
        decision = assess_msk_intake_scope("I have fever and cold")

        self.assertIsNotNone(decision)
        self.assertEqual(decision.category, "respiratory_or_fever")
        self.assertFalse(decision.stop_interview)
        self.assertEqual(decision.patient_message, NON_MSK_INTAKE_MESSAGE)

    def test_other_clear_non_msk_presentations_are_redirected(self):
        for text, category in (
            ("I have vomiting and diarrhea", "gastrointestinal"),
            ("I have a skin rash", "skin_or_allergy"),
            ("I have eye pain", "eye_or_ear"),
            ("I have tooth pain", "dental"),
        ):
            with self.subTest(text=text):
                decision = assess_msk_intake_scope(text)
                self.assertIsNotNone(decision)
                self.assertEqual(decision.category, category)

    def test_msk_context_is_not_redirected_by_an_incidental_word(self):
        self.assertIsNone(assess_msk_intake_scope("Cold makes my knee pain worse"))
        self.assertIsNone(assess_msk_intake_scope("I have lower back pain"))

    def test_empty_or_unclear_text_is_not_diagnosed_by_scope_rules(self):
        self.assertIsNone(assess_msk_intake_scope(""))
        self.assertIsNone(assess_msk_intake_scope("I do not feel well"))

    def test_scope_boundary_precedes_all_ai_intake_routes(self):
        backend_root = Path(__file__).resolve().parents[1]
        source = (backend_root / "server.py").read_text(encoding="utf-8")
        urgent_gate = source.index("_escalation = assess_urgent_risk(")
        scope_gate = source.index("_scope_decision = assess_msk_intake_scope(text_input)")
        off_topic = source.index("# ── Off-topic question shortcut")
        graph_path = source.index("# ── LANGGRAPH PATH")

        self.assertLess(urgent_gate, scope_gate)
        self.assertLess(scope_gate, off_topic)
        self.assertLess(scope_gate, graph_path)

    def test_scope_clarification_is_a_chat_message_without_socket_close(self):
        source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
        start = source.index("_scope_decision = assess_msk_intake_scope(text_input)")
        end = source.index("# ── Off-topic question shortcut", start)
        flow = source[start:end]
        self.assertIn("await send_text_message(", flow)
        self.assertNotIn("websocket.close", flow)
        self.assertNotIn('"type": "clinical_escalation"', flow)
        self.assertIn('client_state["scope_notice_sent"] = True', flow)


if __name__ == "__main__":
    unittest.main()
