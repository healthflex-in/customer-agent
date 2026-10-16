from pathlib import Path
import unittest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class CompletedAttemptLockRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        cls.interview = (
            BACKEND_ROOT / "app" / "ws" / "handlers" / "interview.py"
        ).read_text(encoding="utf-8")

    def test_start_interview_locks_before_any_question_or_summary_ai_work(self):
        start = self.interview.index("async def handle_start_interview")
        end = self.interview.index("async def handle_start_new_form", start)
        flow = self.interview[start:end]

        lookup = flow.index("_start_existing_form = await run_blocking(")
        lock = flow.index("if is_completed_form(_start_existing_form):")
        prom_loading = flow.index("# Fetch tagged questions + build PROM form template")

        self.assertLess(lookup, lock)
        self.assertLess(lock, prom_loading)
        self.assertIn("await send_completed_form(", flow[lock:prom_loading])
        self.assertIn("return", flow[lock:prom_loading])
        # Reconnect/resume no longer runs the legacy HealthAgent summary path.
        self.assertNotIn("health_agent.generate_summary", flow)

    def test_load_form_locks_before_agent_hydration_and_summary_generation(self):
        start = self.interview.index("async def handle_load_form")
        flow = self.interview[start:]

        lock = flow.index("if is_completed_form(form):")
        hydrate = flow.index("health_agent.form = form_data_copy")
        summary = flow.index("health_agent.generate_summary")

        self.assertLess(lock, hydrate)
        self.assertLess(lock, summary)
        self.assertIn("await send_completed_form(websocket, client_state, form)", flow)

    def test_patient_socket_cannot_allocate_a_new_attempt(self):
        start = self.interview.index("async def handle_start_new_form")
        end = self.interview.index("async def handle_load_form", start)
        flow = self.interview[start:end]

        self.assertIn("CLINICIAN_ASSIGNMENT_REQUIRED", flow)
        self.assertNotIn("create_placeholder_form", flow)
        self.assertNotIn("resolve_form_attempt_id", flow)

    def test_completed_payload_is_locked_and_attachment_route_rejects_writes(self):
        payload = (
            BACKEND_ROOT / "app" / "ws" / "completion.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"type": "form_completed"', payload)
        self.assertIn('"locked": True', payload)
        self.assertIn('"progress": 100.0', payload)

        upload_source = (
            BACKEND_ROOT / "app" / "api" / "forms.py"
        ).read_text(encoding="utf-8")
        upload_start = upload_source.index("async def upload_form_attachment")
        upload_flow = upload_source[upload_start:]
        self.assertIn("if is_completed_form(form):", upload_flow)
        self.assertIn("status_code=409", upload_flow)


if __name__ == "__main__":
    unittest.main()
