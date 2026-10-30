"""Ask counts survive a page refresh, keyed like resume: userId + formId + attemptId."""

import unittest

from app.interview.attempt_memory import (
    AttemptMemory,
    attempt_key,
    remember_attempt_counters,
    restore_attempt_counters,
)
from src.forms.loader import load_form
from src.graph.pure_functions.resume import build_resume_plan
from src.prompts import INITIAL_INTAKE_PROMPT


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def socket(attempt_id="ATT-1", user_id="u1", form_id="FRM-01", counts=None) -> dict:
    return {
        "user_id": user_id,
        "form_id": form_id,
        "attempt_id": attempt_id,
        "graph_question_ask_counts": dict(counts or {}),
    }


class AttemptMemoryTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.memory = AttemptMemory(ttl_seconds=100, max_entries=2, clock=self.clock)

    def test_a_refresh_restores_the_counts_for_the_same_attempt(self):
        remember_attempt_counters(socket(counts={"Pain Assessment.Severity (1-10)": 2}), self.memory)

        fresh = socket()
        self.assertTrue(restore_attempt_counters(fresh, self.memory))
        self.assertEqual(fresh["graph_question_ask_counts"], {"Pain Assessment.Severity (1-10)": 2})

    def test_counts_are_kept_apart_per_user_form_and_attempt(self):
        remember_attempt_counters(socket(counts={"a": 1}), self.memory)
        remember_attempt_counters(socket(attempt_id="ATT-2", counts={"a": 3}), self.memory)

        other_attempt = socket(attempt_id="ATT-2")
        other_user = socket(user_id="u2")
        other_form = socket(form_id="FRM-02")
        restore_attempt_counters(other_attempt, self.memory)

        self.assertEqual(other_attempt["graph_question_ask_counts"], {"a": 3})
        self.assertFalse(restore_attempt_counters(other_user, self.memory))
        self.assertFalse(restore_attempt_counters(other_form, self.memory))

    def test_a_legacy_attempt_without_an_id_is_still_keyed(self):
        remember_attempt_counters(socket(attempt_id=None, counts={"a": 2}), self.memory)

        fresh = socket(attempt_id=None)
        self.assertTrue(restore_attempt_counters(fresh, self.memory))
        self.assertEqual(fresh["graph_question_ask_counts"], {"a": 2})

    def test_nothing_is_kept_without_a_user_or_form(self):
        self.assertIsNone(attempt_key({"form_id": "FRM-01"}))
        self.assertIsNone(attempt_key({"user_id": "u1"}))
        remember_attempt_counters({"graph_question_ask_counts": {"a": 1}}, self.memory)
        self.assertFalse(restore_attempt_counters({}, self.memory))

    def test_entries_expire(self):
        remember_attempt_counters(socket(counts={"a": 1}), self.memory)

        self.clock.now = 100
        self.assertFalse(restore_attempt_counters(socket(), self.memory))

    def test_the_store_is_bounded_and_drops_the_oldest(self):
        for attempt in ("ATT-1", "ATT-2", "ATT-3"):
            remember_attempt_counters(socket(attempt_id=attempt, counts={"a": 1}), self.memory)

        self.assertFalse(restore_attempt_counters(socket(attempt_id="ATT-1"), self.memory))
        self.assertTrue(restore_attempt_counters(socket(attempt_id="ATT-3"), self.memory))

    def test_restored_counts_are_a_copy(self):
        remember_attempt_counters(socket(counts={"a": 1}), self.memory)
        first = socket()
        restore_attempt_counters(first, self.memory)
        first["graph_question_ask_counts"]["a"] = 99

        second = socket()
        restore_attempt_counters(second, self.memory)
        self.assertEqual(second["graph_question_ask_counts"], {"a": 1})


class ResumePlanTests(unittest.TestCase):
    def _form_with_complaint(self) -> dict:
        form = load_form("FRM-01").empty_form()
        for name in form["Present Complaint"]:
            form["Present Complaint"][name] = "Provided"
        return form

    def test_resume_asks_mandatory_questions_first_like_a_live_turn(self):
        plan = build_resume_plan(self._form_with_complaint())

        self.assertEqual(
            plan.question_field_ids[:3],
            [
                "Pain Assessment.Primary Location of Pain",
                "Pain Assessment.Severity (1-10)",
                "Pain Assessment.Aggravating Factors",
            ],
        )

    def test_resume_message_names_the_questions_it_asks(self):
        plan = build_resume_plan(self._form_with_complaint())

        entry = plan.agent_entry(plan.message)

        self.assertEqual(entry["question_field_ids"], plan.question_field_ids)

    def test_a_scope_message_in_place_of_the_questions_carries_no_ids(self):
        plan = build_resume_plan(self._form_with_complaint())

        self.assertNotIn("question_field_ids", plan.agent_entry("Please tell me about your visit."))

    def test_the_opening_prompt_carries_no_ids(self):
        plan = build_resume_plan(load_form("FRM-01").empty_form())

        self.assertEqual(plan.message, INITIAL_INTAKE_PROMPT)
        self.assertNotIn("question_field_ids", plan.agent_entry(plan.message))

    def test_the_resume_entry_stores_the_questions_without_the_welcome_back_lines(self):
        plan = build_resume_plan(self._form_with_complaint())

        entry = plan.agent_entry(plan.message)

        self.assertIn("Welcome back", plan.message)
        self.assertNotIn("Welcome back", entry["question_text"])
        self.assertIn("Where exactly do you feel the pain", entry["question_text"])

    def test_a_complete_form_resumes_to_the_summary_without_ids(self):
        form = load_form("FRM-01").empty_form()
        for fields in form.values():
            for name in fields:
                fields[name] = "Provided"
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Accountant; Activity/exercise: Walking"

        plan = build_resume_plan(form)

        self.assertEqual(plan.phase, "summary")
        self.assertNotIn("question_field_ids", plan.agent_entry(plan.message))


if __name__ == "__main__":
    unittest.main()
