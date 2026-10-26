import json
import sys
import types
import unittest
from unittest.mock import patch

from src.forms.loader import load_form
from src.graph.pure_functions.form_extraction import extract_form_data_from_text
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.generate import make_generate_question_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    langgraph_module = types.ModuleType("langgraph")
    langgraph_config_module = types.ModuleType("langgraph.config")
    langgraph_config_module.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", langgraph_module)
    sys.modules["langgraph.config"] = langgraph_config_module
    from src.graph.nodes.generate import make_generate_question_node


class RetiredReferralRegressionTests(unittest.TestCase):
    def test_frm01_schema_has_no_referral_section(self):
        definition = load_form("FRM-01")
        self.assertNotIn("Referral", definition.sections)
        self.assertNotIn("Referral", definition.empty_form())

    def test_completed_interview_generates_summary_without_referral_question(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state["phase"] = "interviewing"
        for fields in state["form"].values():
            for field in fields:
                fields[field] = "Patient-provided clinical answer"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: developer; Activity/exercise: gym; Smoking: does not smoke; "
            "Alcohol: does not drink alcohol"
        )

        with patch("src.graph.nodes.generate.get_stream_writer", return_value=lambda _event: None):
            result = make_generate_question_node(lambda _prompt: "unused", "unused")(state)

        self.assertEqual(result["phase"], "summary")
        self.assertIn("Here's a summary", result["response_text"])
        self.assertNotIn("where did you hear", result["response_text"].lower())
        self.assertNotIn("Referral", result["form"])

    def test_injected_referral_is_ignored_by_extraction(self):
        form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
        form["Referral"] = {"Source": "Injected legacy value"}

        extracted = extract_form_data_from_text(
            "I have knee pain",
            form,
            "extract",
            lambda _prompt: json.dumps({"Referral": {"Source": "Google"}}),
        )

        self.assertNotIn("Referral", extracted)
        self.assertEqual(form["Referral"]["Source"], "Injected legacy value")


if __name__ == "__main__":
    unittest.main()


class LegacyLoadReferralRetirementTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_form_does_not_hydrate_or_validate_historical_referral(self):
        import copy
        import sys
        import types

        if "dotenv" not in sys.modules:
            dotenv_module = types.ModuleType("dotenv")
            dotenv_module.load_dotenv = lambda *_args, **_kwargs: None
            sys.modules["dotenv"] = dotenv_module
        if "fastapi" not in sys.modules:
            fastapi_module = types.ModuleType("fastapi")
            fastapi_module.WebSocket = object
            sys.modules["fastapi"] = fastapi_module
        if "bson" not in sys.modules:
            class ObjectId(str):
                @staticmethod
                def is_valid(_value):
                    return False

            bson_module = types.ModuleType("bson")
            bson_module.ObjectId = ObjectId
            sys.modules["bson"] = bson_module
        users_module = types.ModuleType("app.db.users")
        users_module.validate_user_exists = lambda _user_id: True
        sys.modules["app.db.users"] = users_module

        from app.ws.handlers.interview import handle_load_form

        definition = load_form("FRM-01")
        active_form = definition.empty_form()
        for fields in active_form.values():
            for field in fields:
                fields[field] = "Patient answer"
        active_form["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: teacher; Activity/exercise: walking"
        )
        stored_form = copy.deepcopy(active_form)
        stored_form["Referral"] = {"Source": "", "metadata": {"exact": [1, 2]}}
        stored_before = copy.deepcopy(stored_form)

        class WebSocket:
            def __init__(self):
                self.sent = []

            async def send_text(self, payload):
                self.sent.append(json.loads(payload))

        class Agent:
            def __init__(self):
                self.form = {}
                self.form_sections = list(definition.sections)
                self.current_section = ""
                self.history = []
                self.welcome_prompt = "Welcome"
                self.conversation_state = {}
                self.validated = []
                self.idx = 0

            def init_form(self):
                self.form = definition.empty_form()
                self.form_sections = list(definition.sections)

            def validator(self, section):
                self.validated.append(section)
                return all(str(value).strip() for value in self.form[section].values())

            def generate_summary(self):
                return "Summary"

        persisted = {
            "formId": "FRM-01",
            "attemptId": "ATT-1",
            "userId": "user-1",
            "title": "Existing intake",
            "current_section": "Referral",
            "form_data": stored_form,
        }
        websocket = WebSocket()
        agent = Agent()
        client_state = {"user_id": "user-1", "session_id": "session-1"}
        sent_messages = []

        async def fake_run_blocking(function, *args, **kwargs):
            return function(*args, **kwargs)

        async def build_state(_client_state, **_kwargs):
            return {"phase": "summary"}

        async def send_message(*args, **kwargs):
            sent_messages.append((args, kwargs))

        with patch(
            "app.ws.handlers.interview.fetch_form_by_id",
            return_value=persisted,
        ), patch(
            "app.ws.handlers.interview.run_blocking",
            side_effect=fake_run_blocking,
        ):
            await handle_load_form(
                websocket,
                {"formId": "FRM-01", "attemptId": "ATT-1"},
                client_state,
                agent,
                send_text_message=send_message,
                build_interview_state_async=build_state,
            )

        self.assertNotIn("Referral", agent.form)
        self.assertNotIn("Referral", agent.validated)
        self.assertEqual(agent.idx, 3)
        self.assertEqual(agent.current_section, "")
        self.assertNotIn("Referral", websocket.sent[0]["form_data"])
        self.assertEqual(stored_form, stored_before)
        self.assertEqual(sent_messages[0][0][2], "Summary")
