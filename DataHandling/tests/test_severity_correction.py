"""Changing severity at the summary: decimals round up, ranges take the top, and the reply says what changed."""

import sys
import types
import unittest

from src.graph.pure_functions.additional_complaint import capture_additional_complaint
from src.graph.pure_functions.complaint_severity import (
    _round_scores_up,
    describe_severity_updates,
    explicit_severity_updates,
)
from src.graph.pure_functions.summary import SUMMARY_FOLLOW_UP
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.correction import make_apply_correction_node, make_detect_correction_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    config = types.ModuleType("langgraph.config")
    config.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
    sys.modules["langgraph.config"] = config
    from src.graph.nodes.correction import make_apply_correction_node, make_detect_correction_node


LOCATION = "Upper back, neck bone, and left side of shoulder blade"
SEVERITY_KEY = ("Pain Assessment", "Severity (1-10)")


def one_complaint_form() -> dict:
    form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
    form["Present Complaint"]["Primary Complaint"] = "Severe pain in upper back, neck, and shoulder blade"
    form["Pain Assessment"]["Primary Location of Pain"] = LOCATION
    form["Pain Assessment"]["Severity (1-10)"] = "Not provided"
    return form


def updates_for(text: str, form: dict | None = None) -> dict:
    return explicit_severity_updates(form or one_complaint_form(), text, "Pain Assessment")


class RoundedScoreTests(unittest.TestCase):
    def test_a_decimal_rounds_up_to_the_next_whole_number(self):
        for text, expected in (
            ("severity is 8.6", "9/10"),
            ("severity is 8.2", "9/10"),
            ("severity is 8.0", "8/10"),
            ("severity is 9.9", "10/10"),
            ("severity is 10.0", "10/10"),
            ("it is 7.5/10", "8/10"),
        ):
            with self.subTest(text=text):
                self.assertEqual(updates_for(text), {SEVERITY_KEY: expected})

    def test_a_range_takes_the_highest_number(self):
        for text in ("severity is 8-9", "severity is 8 to 9", "severity is 8–9", "severity is 8-9/10"):
            with self.subTest(text=text):
                self.assertEqual(updates_for(text), {SEVERITY_KEY: "9/10"})

    def test_a_whole_number_is_unchanged(self):
        self.assertEqual(updates_for("severity is 9"), {SEVERITY_KEY: "9/10"})
        self.assertEqual(updates_for("severity is 8"), {SEVERITY_KEY: "8/10"})

    def test_a_quoted_scale_is_not_treated_as_a_range(self):
        for text in ("on a scale of 1 to 10", "on a scale of 0 to 10", "0-10", "1-10"):
            with self.subTest(text=text):
                self.assertEqual(_round_scores_up(text), text)

    def test_a_reply_that_quotes_the_0_to_10_scale_still_gives_the_score(self):
        updates = explicit_severity_updates(
            one_complaint_form(), "on 0 to 10 it is 7", "Pain Assessment",
            "On a scale of 0 to 10, how severe is it right now?",
        )

        self.assertEqual(updates, {SEVERITY_KEY: "7/10"})

    def test_a_decimal_that_is_not_a_score_changes_nothing(self):
        self.assertEqual(updates_for("I have had it for 2.5 years"), {})

    def test_a_score_above_the_scale_is_ignored(self):
        self.assertEqual(updates_for("severity is 11.5"), {})


class SeverityMessageTests(unittest.TestCase):
    def _correct(self, text: str, form: dict | None = None) -> dict:
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            phase="summary",
            user_input=text,
            form=form or one_complaint_form(),
            history=[{"role": "agent", "message": "Here's a summary"}],
        )
        detected = make_detect_correction_node(lambda _prompt: "{}")(state)
        return make_apply_correction_node(lambda _prompt: "{}")({**state, **detected})

    def test_the_reply_says_the_severity_was_updated(self):
        result = self._correct("severity is 8.6")

        text = result["response_text"]
        self.assertTrue(text.startswith("I've updated your severity to 9/10."))
        self.assertTrue(text.endswith(SUMMARY_FOLLOW_UP))

    def test_the_updated_summary_is_shown_with_the_change(self):
        text = self._correct("severity is 8.6")["response_text"]

        self.assertIn("Here's your updated summary:", text)
        self.assertIn("- Severity (1-10): 9/10", text)
        self.assertEqual(text.count(SUMMARY_FOLLOW_UP), 1)

    def test_the_reply_does_not_name_the_location_as_if_it_changed(self):
        result = self._correct("severity is 8.6")

        self.assertNotIn(LOCATION, result["response_text"].split("\n\n")[0])
        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "9/10")
        self.assertEqual(result["form"]["Pain Assessment"]["Primary Location of Pain"], LOCATION)

    def test_with_two_complaints_the_reply_names_which_one(self):
        form = one_complaint_form()
        form["Pain Assessment"]["Severity (1-10)"] = "6/10"
        form, section = capture_additional_complaint(form, "I also have knee pain")
        updates = {SEVERITY_KEY: "8/10", (section, "Severity (1-10)"): "6/10"}

        phrases = describe_severity_updates(form, updates)

        self.assertEqual(phrases[0], f"your severity for {LOCATION} to 8/10")
        self.assertTrue(phrases[1].startswith("your severity for ") and phrases[1].endswith(" to 6/10"))
        self.assertIn("knee", phrases[1].lower())


if __name__ == "__main__":
    unittest.main()
