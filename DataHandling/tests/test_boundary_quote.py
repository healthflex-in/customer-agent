"""A boundary reply repeats only the question, not the notes said in front of it."""

import unittest

from src.graph.pure_functions.clarification import (
    OUT_OF_FLOW_RESPONSE,
    boundary_response_with_pending_question,
    pending_intake_question,
)


NOTE = "Thanks, I've noted how it started and what may have caused it."
QUESTION = "How long have you been experiencing this issue?"


class PendingQuestionTests(unittest.TestCase):
    def test_it_quotes_the_question_without_the_acknowledgement_before_it(self):
        history = [{"role": "agent", "message": f"{NOTE}\n\n{QUESTION}", "question_text": QUESTION}]

        self.assertEqual(pending_intake_question(history), QUESTION)

    def test_an_older_message_without_question_text_is_quoted_whole_as_before(self):
        history = [{"role": "agent", "message": QUESTION}]

        self.assertEqual(pending_intake_question(history), QUESTION)

    def test_the_boundary_reply_ends_with_only_the_question(self):
        history = [{"role": "agent", "message": f"{NOTE}\n\n{QUESTION}", "question_text": QUESTION}]

        reply = boundary_response_with_pending_question(OUT_OF_FLOW_RESPONSE, pending_intake_question(history))

        self.assertTrue(reply.endswith(QUESTION))
        self.assertNotIn(NOTE, reply)

    def test_a_previous_boundary_reply_is_skipped(self):
        history = [
            {"role": "agent", "message": QUESTION, "question_text": QUESTION},
            {"role": "agent", "message": OUT_OF_FLOW_RESPONSE + "\n\nmore"},
        ]

        self.assertEqual(pending_intake_question(history), QUESTION)


if __name__ == "__main__":
    unittest.main()
