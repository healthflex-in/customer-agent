"""A question is put to the patient at most MAX_TIMES_ASKED times, whatever the patient does.

These drive ``next_intake_step`` turn by turn, the way the graph does, and
count every field the bot shows. They cover the patient who never answers and
the patient who declines everything, which is where the old loop came from.

After the last ask a question is closed as Not provided and the interview moves
on. For a mandatory question the patient is also told to raise it with their
clinician.
"""

import unittest
from collections import Counter

from src.forms.loader import load_form
from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step
from src.graph.pure_functions.answer_mapping import DECLINED, NOT_PROVIDED, apply_declines
from src.graph.pure_functions.question_plan import MANDATORY_LATER


MAX_TURNS = 60
SEVERITY = "Pain Assessment.Severity (1-10)"


def _fresh_state() -> dict:
    form = load_form("FRM-01").empty_form()
    return {
        "form": form,
        "form_sections": list(form),
        "visit_context": "specific_complaint",
        "question_ask_counts": {},
        "tagged_form_template": None,
        "history": [],
        "decline_notice": False,
        "noted_complaints": [],
        "end_requested": False,
        "completion_percentage": 0,
    }


def _run_interview(patient) -> tuple[dict, Counter, list[str]]:
    """Play turns until the summary; return final state, show counts, and every reply sent."""

    state = _fresh_state()
    shown: Counter = Counter()
    replies: list[str] = []
    for _ in range(MAX_TURNS):
        before = dict(state["question_ask_counts"])
        patch = next_intake_step(state, state["history"])
        state.update(patch)
        replies.append(patch["response_text"])
        asked_now = [
            field_id for field_id, count in state["question_ask_counts"].items()
            if count > before.get(field_id, 0)
        ]
        for field_id in asked_now:
            shown[field_id] += 1
        if state["phase"] == "summary":
            return state, shown, replies
        patient(state)
    raise AssertionError("interview did not reach the summary within the turn budget")


def _saved_values(form: dict) -> set:
    return {
        str(value)
        for fields in form.values() if isinstance(fields, dict)
        for value in fields.values()
    }


def _silent_patient(state: dict) -> None:
    """Never answers anything."""


def _decline_everything(state: dict) -> None:
    """Refuses every question that was just asked."""

    form, _closed = apply_declines(state["form"], list(state["question_field_ids"]))
    state["form"] = form


class QuestionAskCapTests(unittest.TestCase):
    def test_silent_patient_sees_each_question_at_most_max_times(self):
        state, shown, _ = _run_interview(_silent_patient)

        self.assertTrue(shown, "the bot should ask at least one question")
        self.assertLessEqual(max(shown.values()), MAX_TIMES_ASKED)
        self.assertIn(NOT_PROVIDED, _saved_values(state["form"]))

    def test_declining_patient_sees_each_question_at_most_max_times(self):
        state, shown, _ = _run_interview(_decline_everything)

        self.assertTrue(shown)
        self.assertLessEqual(max(shown.values()), MAX_TIMES_ASKED)
        self.assertIn(DECLINED, _saved_values(state["form"]), "refused optional questions close as Declined")

    def test_optional_question_closes_as_not_provided_after_the_last_ask(self):
        field_id = "Pain Assessment.Relieving Factors"
        state = _fresh_state()
        state["question_ask_counts"] = {field_id: MAX_TIMES_ASKED}

        patch = next_intake_step(state, [])

        self.assertNotIn(field_id, patch["question_field_ids"])
        self.assertEqual(patch["form"]["Pain Assessment"]["Relieving Factors"], NOT_PROVIDED)

    def test_mandatory_question_is_closed_after_the_last_ask_and_the_patient_is_told(self):
        state = _fresh_state()
        state["question_ask_counts"] = {SEVERITY: MAX_TIMES_ASKED}

        patch = next_intake_step(state, [])

        self.assertNotIn(SEVERITY, patch["question_field_ids"])
        self.assertEqual(patch["form"]["Pain Assessment"]["Severity (1-10)"], NOT_PROVIDED)
        self.assertIn(MANDATORY_LATER.format(labels="Severity"), patch["response_text"])

    def test_every_mandatory_field_is_shown_exactly_max_times_then_closed(self):
        state, shown, _ = _run_interview(_silent_patient)

        for field in load_form("FRM-01").required_fields("specific_complaint"):
            ids = [field_id for field_id in shown if field_id.endswith(f".{field}")]
            self.assertTrue(ids, f"{field} was never asked")
            for field_id in ids:
                self.assertEqual(shown[field_id], MAX_TIMES_ASKED)
                section, name = field_id.split(".", 1)
                self.assertEqual(state["form"][section][name], NOT_PROVIDED)

    def test_a_patient_who_never_answers_reaches_the_summary_and_is_told_what_to_raise_later(self):
        state, _, replies = _run_interview(_silent_patient)

        self.assertEqual(state["phase"], "summary")
        told = " ".join(replies)
        self.assertIn("I wasn't able to capture", told)
        self.assertIn("tell your clinician about this later", told)


if __name__ == "__main__":
    unittest.main()
