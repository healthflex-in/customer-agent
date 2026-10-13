from __future__ import annotations

import unittest

from app.forms.prom import (
    PROM_COMPLETION_MESSAGE,
    advance_tagged_turn,
    parse_structured_prom_answers,
)


class StructuredPromAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.meta = {
            "type": "multi_answer",
            "question_ids": ["prom_nps_score", "prom_phq9_interest"],
            "question_types": ["scale", "single_choice"],
            "question_options": [
                None,
                ["Not at all", "Several days", "More than half the days"],
            ],
        }

    def test_accepts_exact_validated_ui_answers(self) -> None:
        result = parse_structured_prom_answers(
            "7|Several days", self.meta, "structured_prom"
        )

        self.assertEqual(result, ["7", "Several days"])

    def test_rejects_voice_or_free_form_input_without_protocol_delimiter(self) -> None:
        self.assertIsNone(
            parse_structured_prom_answers("seven and several days", self.meta)
        )

    def test_rejects_missing_or_extra_answers(self) -> None:
        self.assertIsNone(
            parse_structured_prom_answers("7|", self.meta, "structured_prom")
        )
        self.assertIsNone(
            parse_structured_prom_answers(
                "7|Several days|extra", self.meta, "structured_prom"
            )
        )

    def test_rejects_an_answer_outside_configured_options(self) -> None:
        self.assertIsNone(
            parse_structured_prom_answers("7|Sometimes", self.meta, "structured_prom")
        )

    def test_rejects_a_scale_outside_zero_to_ten(self) -> None:
        self.assertIsNone(
            parse_structured_prom_answers(
                "11|Several days", self.meta, "structured_prom"
            )
        )

    def test_rejects_single_question_payload_without_explicit_source_metadata(self) -> None:
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_nps_score"],
            "question_types": ["scale"],
            "question_options": [None],
        }

        self.assertIsNone(parse_structured_prom_answers("7", meta, "structured_prom"))

    def test_accepts_valid_boolean_and_positional_text_answers(self) -> None:
        meta = {
            "type": "multi_answer",
            "question_ids": ["prom_rmdq_1", "biz_feedback_comment"],
            "question_types": ["yes_no", "text"],
            "question_options": [None, None],
        }

        self.assertEqual(
            parse_structured_prom_answers(
                "Yes|Very helpful", meta, "structured_prom"
            ),
            ["Yes", "Very helpful"],
        )

    def test_rejects_incomplete_or_unknown_question_metadata(self) -> None:
        incomplete_meta = {**self.meta, "question_types": ["scale"]}
        unknown_meta = {**self.meta, "question_types": ["custom", "single_choice"]}

        self.assertIsNone(
            parse_structured_prom_answers(
                "7|Several days", incomplete_meta, "structured_prom"
            )
        )
        self.assertIsNone(
            parse_structured_prom_answers(
                "7|Several days", unknown_meta, "structured_prom"
            )
        )

    def test_rejects_delimited_text_without_explicit_structured_mode(self) -> None:
        self.assertIsNone(parse_structured_prom_answers("7|Several days", self.meta))


class TaggedTurnAdvanceTests(unittest.TestCase):
    def test_returns_next_turn_meta_and_incremented_cursor(self) -> None:
        result = advance_tagged_turn(
            ["first", "second"], [{"type": "text"}, {"type": "multi_answer"}], 1
        )

        self.assertEqual(result, ("second", {"type": "multi_answer"}, 2, False))

    def test_returns_completion_without_advancing_past_end(self) -> None:
        result = advance_tagged_turn(["first"], [{"type": "text"}], 1)

        self.assertEqual(result, (PROM_COMPLETION_MESSAGE, None, 1, True))


if __name__ == "__main__":
    unittest.main()
