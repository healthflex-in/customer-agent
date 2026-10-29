"""The websocket handler refuses only the clear cases; an open message goes to the graph."""

import unittest
from pathlib import Path

from src.graph.pure_functions.clarification import out_of_flow_kind


HANDLER = Path(__file__).resolve().parents[1] / "app" / "ws" / "handlers" / "text.py"
QUESTION = "How long have you been experiencing this issue?"

ORDINARY_ANSWERS = (
    "may be aa few days", "may be from few days", "may be few days", "could be two weeks",
    "can't remember exactly", "do not remember", "does not matter, about a week",
    "will say about 3 days", "where my hip meets my leg", "which one, the left side",
    "what makes it worse is sitting", "when I run", "when I climb stairs",
    "how it started was gradual", "is hard to say, maybe gradual", "am not sure, gradual I think",
)

ADVICE = (
    "what exercise should I do for this?", "should I take ibuprofen?", "what could it be?",
    "tell me about stance health",
)


class HandlerGateTests(unittest.TestCase):
    def _boundary_flow(self) -> str:
        """The handler's boundary gate, up to the (disabled) off-topic shortcut after it."""
        source = HANDLER.read_text(encoding="utf-8")
        start = source.index("_boundary_response = (")
        return source[start:source.index("# ── Off-topic question shortcut", start)]

    def test_the_handler_defers_open_messages_to_the_graph(self):
        flow = self._boundary_flow()

        self.assertIn('out_of_flow_kind(text_input, _last_agent_question) == "open"', flow)
        self.assertIn("_boundary_response = None", flow)

    def test_the_handler_still_refuses_advice_itself_without_asking_a_model(self):
        flow = self._boundary_flow()

        self.assertIn("build_out_of_flow_response(", flow)
        self.assertNotIn("llm", flow.lower())


class NoOrdinaryAnswerIsRefusedOutrightTests(unittest.TestCase):
    def test_an_ordinary_answer_is_never_a_hard_refusal(self):
        """"hard" is refused by the handler with no model involved, so an answer must never be one."""

        for text in ORDINARY_ANSWERS:
            with self.subTest(text=text):
                self.assertNotEqual(out_of_flow_kind(text, QUESTION), "hard")

    def test_advice_and_clinic_questions_stay_hard(self):
        for text in ADVICE:
            with self.subTest(text=text):
                self.assertEqual(out_of_flow_kind(text, QUESTION), "hard")


if __name__ == "__main__":
    unittest.main()
