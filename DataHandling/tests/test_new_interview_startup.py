from pathlib import Path
import unittest

from app.forms.lifecycle import has_meaningful_form_data


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class NewInterviewStartupRegressionTests(unittest.TestCase):
    def test_empty_new_attempt_is_safe_and_has_no_meaningful_data(self):
        self.assertFalse(has_meaningful_form_data({}))

    def test_new_frm01_sends_only_single_opening_prompt(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        start = source.index("# NEW INTERVIEW MODE: Verify reset and create new form")
        end = source.index('elif msg_type == "start_new_form"', start)
        flow = source[start:end]

        safe_check = flow.index("form_has_data = has_meaningful_form_data(form_data)")
        welcome = flow.index("_welcome_text = INITIAL_INTAKE_PROMPT")
        first_send = flow.index("await send_text_message(", welcome)
        prom_guard = flow.index("if _tagged_turns:", first_send)

        self.assertLess(safe_check, welcome)
        self.assertLess(first_send, prom_guard)
        self.assertNotIn("first_question = PREDEFINED_QUESTIONS[0][1]", flow)
        self.assertIn("if _tagged_turns:", flow)
        self.assertNotIn("agent state contains data at {section}.{field}", flow)

    def test_first_attempt_is_persisted_before_opening_messages(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        helper_start = source.index("def create_placeholder_form(")
        helper_end = source.index("def build_interview_state(", helper_start)
        helper = source[helper_start:helper_end]

        self.assertIn("saved_form_id = save_customer_info(", helper)
        self.assertIn("form_data={}", helper)
        self.assertIn('current_section="Present Complaint"', helper)
        self.assertIn('raise RuntimeError("Unable to reserve', helper)

    def test_unknown_attempt_recovery_is_limited_to_first_form(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        start = source.index("if provided_attempt_id and not existing_form:")
        end = source.index("# Check if user already has a form", start)
        recovery = source[start:end]

        self.assertIn("_any_existing_attempt", recovery)
        self.assertIn('"code": "ATTEMPT_NOT_FOUND"', recovery)
        self.assertIn("continue", recovery)

    def test_empty_reserved_draft_uses_single_opening_prompt(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        start = source.index("# Resume must be deterministic.")
        end = source.index("await send_text_message(", start)
        selection = source[start:end]

        self.assertIn("elif not _saved_patient_content:", selection)
        self.assertLess(selection.index("elif not _saved_patient_content:"), selection.index("elif not _required_missing:"))
        self.assertIn("resume_message = INITIAL_INTAKE_PROMPT", selection)

        resume_start = source.index("client_state[\"graph_history\"]", start)
        next_question = source.index("continue\n                            else:", resume_start)
        empty_draft_guard = source[resume_start:next_question]
        self.assertIn(
            'if not _saved_patient_content and not client_state.get("tagged_turns"):',
            empty_draft_guard,
        )
        self.assertIn("continue", empty_draft_guard)

    def test_resuming_never_uses_legacy_ai_summary(self):
        source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        start = source.index("# Resume must be deterministic.")
        end = source.index("else:\n                                print(\"[start_interview] Requested form not found", start)
        resume_flow = source[start:end]

        self.assertNotIn("health_agent.generate_summary", resume_flow)
        self.assertNotIn("health_agent.talk_to_user", resume_flow)
        self.assertIn("contextual_resume_question", resume_flow)

    def test_short_clinical_first_reply_is_not_treated_as_confirmation(self):
        source = (
            BACKEND_ROOT / "src" / "graph" / "nodes" / "first_turn.py"
        ).read_text(encoding="utf-8")

        self.assertIn("if _normalized in _CONFIRMATIONS:", source)
        self.assertNotIn("or len(user_input.strip()) < 20", source)


if __name__ == "__main__":
    unittest.main()
