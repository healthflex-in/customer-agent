"""What a refusal means: decline optional questions, protect mandatory ones, ask which when unclear."""

import unittest

from src.forms.loader import load_form
from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step
from src.graph.pure_functions.answer_mapping import decide_skip
from src.graph.pure_functions.question_plan import CLINICIAN_LATER, MANDATORY_NOTICE


SEVERITY = "Pain Assessment.Severity (1-10)"
AGGRAVATING = "Pain Assessment.Aggravating Factors"
RELIEVING = "Pain Assessment.Relieving Factors"
REPORTS = "History & Diagnostics.Reports"
GOALS = "Treatment Goals.Short-Term Goals (within 3 months)"


def decide(asked, *, declined=(), unspecified=False, counts=None, form=None):
    return decide_skip(
        form=form if form is not None else load_form("FRM-01").empty_form(),
        asked_ids=list(asked),
        declined_ids=list(declined),
        unspecified=unspecified,
        ask_counts=counts or {},
        max_asks=MAX_TIMES_ASKED,
    )


class UnspecifiedSkipTests(unittest.TestCase):
    def test_skip_on_only_mandatory_questions_is_refused_politely(self):
        decision = decide([SEVERITY, AGGRAVATING], unspecified=True)

        self.assertTrue(decision.mandatory_refused)
        self.assertEqual(decision.decline_ids, [])
        self.assertEqual(decision.ask_which, [])

    def test_skip_on_a_single_optional_question_declines_it_without_asking(self):
        decision = decide([RELIEVING], unspecified=True)

        self.assertEqual(decision.decline_ids, [RELIEVING])
        self.assertEqual(decision.ask_which, [])
        self.assertFalse(decision.mandatory_refused)

    def test_skip_on_several_optional_questions_asks_which(self):
        decision = decide([RELIEVING, REPORTS, GOALS], unspecified=True)

        self.assertEqual(decision.ask_which, [RELIEVING, REPORTS, GOALS])
        self.assertEqual(decision.decline_ids, [])
        self.assertFalse(decision.keep_required_note)

    def test_mixed_batch_offers_only_the_optional_ones_and_says_the_rest_stay(self):
        decision = decide([SEVERITY, RELIEVING, REPORTS], unspecified=True)

        self.assertEqual(decision.ask_which, [RELIEVING, REPORTS])
        self.assertTrue(decision.keep_required_note)
        self.assertEqual(decision.decline_ids, [])

    def test_mixed_batch_with_one_optional_still_asks_instead_of_guessing(self):
        decision = decide([SEVERITY, RELIEVING], unspecified=True)

        self.assertEqual(decision.ask_which, [RELIEVING])
        self.assertTrue(decision.keep_required_note)
        self.assertEqual(decision.decline_ids, [])

    def test_optional_question_already_at_the_cap_is_not_offered(self):
        decision = decide(
            [RELIEVING, REPORTS], unspecified=True, counts={RELIEVING: MAX_TIMES_ASKED}
        )

        self.assertEqual(decision.decline_ids, [REPORTS])
        self.assertEqual(decision.ask_which, [])

    def test_already_answered_questions_are_not_offered(self):
        form = load_form("FRM-01").empty_form()
        form["History & Diagnostics"]["Reports"] = "No reports"

        decision = decide([RELIEVING, REPORTS], unspecified=True, form=form)

        self.assertEqual(decision.decline_ids, [RELIEVING])


class NamedSkipTests(unittest.TestCase):
    def test_naming_a_mandatory_question_is_refused_and_not_declined(self):
        decision = decide([SEVERITY, RELIEVING], declined=[SEVERITY])

        self.assertTrue(decision.mandatory_refused)
        self.assertEqual(decision.decline_ids, [])

    def test_naming_both_declines_the_optional_and_refuses_the_mandatory(self):
        decision = decide([SEVERITY, RELIEVING], declined=[SEVERITY, RELIEVING])

        self.assertTrue(decision.mandatory_refused)
        self.assertEqual(decision.decline_ids, [RELIEVING])

    def test_naming_an_optional_question_declines_only_that_one(self):
        decision = decide([RELIEVING, REPORTS], declined=[REPORTS])

        self.assertEqual(decision.decline_ids, [REPORTS])
        self.assertFalse(decision.mandatory_refused)


class MandatoryNoticeMessageTests(unittest.TestCase):
    def _state(self, **extra):
        form = load_form("FRM-01").empty_form()
        for fields in form.values():
            for name in fields:
                fields[name] = "Provided"
        form["History & Diagnostics"]["Current Lifestyle"] = "Work: Accountant; Activity/exercise: Walking"
        form["Pain Assessment"]["Severity (1-10)"] = ""
        return {
            "form": form,
            "form_sections": list(form),
            "visit_context": "specific_complaint",
            "question_ask_counts": {},
            "tagged_form_template": None,
            "decline_notice": False,
            "noted_complaints": [],
            **extra,
        }

    def test_refused_mandatory_question_is_explained_and_asked_again(self):
        step = next_intake_step(self._state(mandatory_notice=True), [])

        self.assertTrue(step["response_text"].startswith(MANDATORY_NOTICE))
        self.assertEqual(step["question_field_ids"], [SEVERITY])
        self.assertFalse(step["mandatory_notice"])

    def test_no_notice_when_nothing_was_refused(self):
        step = next_intake_step(self._state(mandatory_notice=False), [])

        self.assertNotIn(MANDATORY_NOTICE, step["response_text"])

    def test_no_notice_when_the_cap_has_just_closed_the_mandatory_question(self):
        state = self._state(mandatory_notice=True, question_ask_counts={SEVERITY: MAX_TIMES_ASKED})
        state["form"]["Pain Assessment"]["Relieving Factors"] = ""

        step = next_intake_step(state, [])

        self.assertNotIn(MANDATORY_NOTICE, step["response_text"])

    def test_notice_comes_with_the_optional_skip_acknowledgement(self):
        step = next_intake_step(self._state(mandatory_notice=True, decline_notice=True), [])

        self.assertIn(MANDATORY_NOTICE, step["response_text"])
        self.assertIn(CLINICIAN_LATER, step["response_text"])


if __name__ == "__main__":
    unittest.main()
