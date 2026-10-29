"""Section-by-section questions, retry wording, the still-needed request, and goodbye handling."""

import os
import unittest
from unittest.mock import patch as patch_env

from app.config import _percent_env
from src.forms.loader import load_form
from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step
from src.graph.pure_functions.completion import completion_status
from src.graph.pure_functions.question_plan import (
    CLINICIAN_LATER,
    END_CONFIRM,
    END_CONTINUE,
    END_NOT_YET_MORE,
    END_NOT_YET_REQUIRED,
    MANDATORY_LATER,
    MANDATORY_NOTICE,
    NOTED_COMPLAINT,
    RETRY_MANDATORY_NOTICE,
    RETRY_NOTICE,
)
from src.graph.pure_functions.summary import SUMMARY_QUESTION


MANDATORY_IDS = [
    "Present Complaint.Primary Complaint",
    "Present Complaint.Duration of the Issue",
    "Present Complaint.Onset (Gradual or Sudden)",
    "Present Complaint.Mechanism of Injury or Cause",
    "Pain Assessment.Primary Location of Pain",
    "Pain Assessment.Severity (1-10)",
    "Pain Assessment.Aggravating Factors",
]
SEVERITY = "Pain Assessment.Severity (1-10)"
RELIEVING = "Pain Assessment.Relieving Factors"


def form_with(*answered_ids: str) -> dict:
    """A blank FRM-01 form with the listed ``Section.Field`` ids answered."""

    form = load_form("FRM-01").empty_form()
    for field_id in answered_ids:
        section, field = field_id.split(".", 1)
        form[section][field] = "Provided"
    return form


def form_filled_except(*blank_ids: str) -> dict:
    """A complete form with the listed ids left blank."""

    form = load_form("FRM-01").empty_form()
    for fields in form.values():
        for name in fields:
            fields[name] = "Provided"
    form["History & Diagnostics"]["Current Lifestyle"] = "Work: Accountant; Activity/exercise: Walking"
    for field_id in blank_ids:
        section, field = field_id.split(".", 1)
        form[section][field] = ""
    return form


def state_for(form: dict, **extra) -> dict:
    return {
        "form": form,
        "form_sections": list(form),
        "visit_context": "specific_complaint",
        "question_ask_counts": {},
        "tagged_form_template": None,
        "decline_notice": False,
        "mandatory_notice": False,
        "end_requested": False,
        "completion_percentage": 0,
        "noted_complaints": [],
        **extra,
    }


def end_and_confirm(state: dict) -> tuple[dict, dict]:
    """Ask to end, then say yes to "are you sure?". Returns (the first reply, the final one)."""

    first = next_intake_step(state, [])
    confirmed = {**state, **first, "end_requested": True, "end_reply": "confirmed"}
    return first, next_intake_step(confirmed, first["history"])


def section_of(field_id: str) -> str:
    return field_id.split(".", 1)[0]


class SectionBatchTests(unittest.TestCase):
    def test_only_the_left_out_questions_of_a_section_are_asked(self):
        answered = [i for i in MANDATORY_IDS if section_of(i) == "Present Complaint" and "Mechanism" not in i]

        step = next_intake_step(state_for(form_with(*answered)), [])

        self.assertEqual(step["question_field_ids"], ["Present Complaint.Mechanism of Injury or Cause"])

    def test_a_batch_never_mixes_sections(self):
        state = state_for(load_form("FRM-01").empty_form())
        for _ in range(30):
            step = next_intake_step(state, [])
            if step["phase"] == "summary" or not step["question_field_ids"]:
                break
            ids = step["question_field_ids"]
            self.assertEqual({section_of(i) for i in ids}, {section_of(ids[0])}, ids)
            state.update(step)
            for field_id in ids:  # the patient answers, so the next section is reached
                section, field = field_id.split(".", 1)
                if field in state["form"][section]:
                    state["form"][section][field] = "Provided"

    def test_a_section_with_mandatory_questions_is_asked_before_optional_sections(self):
        form = form_with(*[i for i in MANDATORY_IDS if section_of(i) == "Present Complaint"])

        step = next_intake_step(state_for(form), [])

        self.assertEqual({section_of(i) for i in step["question_field_ids"]}, {"Pain Assessment"})


class RetryWordingTests(unittest.TestCase):
    def test_the_first_ask_has_no_retry_wording(self):
        step = next_intake_step(state_for(load_form("FRM-01").empty_form()), [])

        self.assertNotIn(RETRY_NOTICE, step["response_text"])
        self.assertNotIn(RETRY_MANDATORY_NOTICE, step["response_text"])

    def test_asking_a_mandatory_question_again_says_it_is_required(self):
        first = next_intake_step(state_for(load_form("FRM-01").empty_form()), [])
        state = state_for(load_form("FRM-01").empty_form(), question_ask_counts=first["question_ask_counts"])

        second = next_intake_step(state, [])

        self.assertTrue(second["response_text"].startswith(RETRY_MANDATORY_NOTICE))
        self.assertEqual(second["question_field_ids"], first["question_field_ids"])

    def test_asking_an_optional_question_again_uses_the_plain_retry(self):
        form = form_filled_except("Treatment Goals.Short-Term Goals (within 3 months)")
        goals = "Treatment Goals.Short-Term Goals (within 3 months)"
        state = state_for(form, question_ask_counts={goals: 1})

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(RETRY_NOTICE))

    def test_a_batch_that_mixes_new_and_repeated_questions_has_no_retry_wording(self):
        form = load_form("FRM-01").empty_form()
        state = state_for(form, question_ask_counts={"Present Complaint.Primary Complaint": 1})

        step = next_intake_step(state, [])

        self.assertNotIn(RETRY_MANDATORY_NOTICE, step["response_text"])


class RetryApologyOnlyForUnregisteredRepliesTests(unittest.TestCase):
    """No "sorry, I didn't catch that" when the reply was understood as something else."""

    GOALS = [
        "Treatment Goals.Short-Term Goals (within 3 months)",
        "Treatment Goals.Long-Term Goals (after 3 months)",
        "Treatment Goals.Specific Expectations from Treatment",
    ]

    def _goals_asked_once(self, **extra) -> dict:
        form = form_filled_except(*self.GOALS)
        return state_for(form, question_ask_counts={goal: 1 for goal in self.GOALS}, **extra)

    def test_a_reply_that_registered_as_nothing_still_gets_the_apology(self):
        step = next_intake_step(self._goals_asked_once(), [])

        self.assertTrue(step["response_text"].startswith(RETRY_NOTICE))

    def test_a_noted_complaint_is_acknowledged_without_an_apology(self):
        step = next_intake_step(self._goals_asked_once(noted_complaints=["Shoulder pain"]), [])

        self.assertTrue(step["response_text"].startswith(NOTED_COMPLAINT.format(labels="Shoulder pain")))
        self.assertNotIn(RETRY_NOTICE, step["response_text"])
        self.assertEqual(step["question_field_ids"], self.GOALS)

    def test_an_accepted_skip_is_acknowledged_without_an_apology(self):
        step = next_intake_step(self._goals_asked_once(decline_notice=True), [])

        self.assertIn(CLINICIAN_LATER, step["response_text"])
        self.assertNotIn(RETRY_NOTICE, step["response_text"])

    def test_the_mandatory_notice_replaces_the_apology(self):
        step = next_intake_step(
            state_for(
                form_filled_except(SEVERITY),
                question_ask_counts={SEVERITY: 1},
                mandatory_notice=True,
            ),
            [],
        )

        self.assertTrue(step["response_text"].startswith(MANDATORY_NOTICE))
        self.assertNotIn(RETRY_MANDATORY_NOTICE, step["response_text"])

    def test_a_mandatory_notice_beside_optional_questions_adds_no_apology(self):
        step = next_intake_step(self._goals_asked_once(mandatory_notice=True), [])

        self.assertNotIn(RETRY_NOTICE, step["response_text"])
        self.assertNotIn(MANDATORY_NOTICE, step["response_text"])

    def test_telling_the_patient_a_mandatory_question_was_closed_adds_no_apology(self):
        form = form_filled_except(SEVERITY, RELIEVING)
        state = state_for(form, question_ask_counts={SEVERITY: MAX_TIMES_ASKED, RELIEVING: 1})

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(MANDATORY_LATER.format(labels="Severity")))
        self.assertNotIn(RETRY_NOTICE, step["response_text"])
        self.assertEqual(step["question_field_ids"], [RELIEVING])

    def test_the_transcript_case_a_new_complaint_while_goals_are_repeated(self):
        step = next_intake_step(self._goals_asked_once(noted_complaints=["shoulder pain"]), [])

        self.assertTrue(step["response_text"].startswith("I've noted shoulder pain."))
        self.assertNotIn("Sorry", step["response_text"])


class MandatoryClosedAtTheCapTests(unittest.TestCase):
    """After three asks a mandatory question is closed and the patient moves on."""

    def _state(self, form, *ids, **extra) -> dict:
        return state_for(form, question_ask_counts={i: MAX_TIMES_ASKED for i in ids}, **extra)

    def test_the_patient_is_told_to_raise_it_with_their_clinician(self):
        step = next_intake_step(self._state(form_filled_except(SEVERITY), SEVERITY), [])

        self.assertTrue(step["response_text"].startswith(MANDATORY_LATER.format(labels="Severity")))

    def test_it_moves_on_to_the_summary_which_asks_yes_or_edit(self):
        step = next_intake_step(self._state(form_filled_except(SEVERITY), SEVERITY), [])

        self.assertEqual(step["phase"], "summary")
        self.assertEqual(step["form"]["Pain Assessment"]["Severity (1-10)"], "Not provided")
        self.assertIn("Here's a summary", step["response_text"])
        self.assertTrue(step["response_text"].endswith(SUMMARY_QUESTION))

    def test_it_moves_on_to_the_next_questions_when_more_remain(self):
        form = form_with(*[i for i in MANDATORY_IDS if section_of(i) == "Present Complaint"])
        step = next_intake_step(self._state(form, SEVERITY), [])

        self.assertEqual(step["phase"], "interviewing")
        self.assertNotIn(SEVERITY, step["question_field_ids"])
        self.assertIn("I wasn't able to capture Severity", step["response_text"])

    def test_every_closed_mandatory_question_is_named_in_plain_words(self):
        form = form_filled_except(SEVERITY, "Pain Assessment.Aggravating Factors")
        counts = (SEVERITY, "Pain Assessment.Aggravating Factors")

        step = next_intake_step(self._state(form, *counts), [])

        self.assertIn("Severity, Aggravating Factors", step["response_text"])
        self.assertNotIn("(1-10)", step["response_text"].split("\n\n")[0])

    def test_an_optional_question_closing_gets_no_clinician_note(self):
        form = form_filled_except(RELIEVING)

        step = next_intake_step(self._state(form, RELIEVING), [])

        self.assertNotIn("I wasn't able to capture", step["response_text"])

    def test_the_patient_is_not_asked_it_again_after_it_is_closed(self):
        state = self._state(form_filled_except(SEVERITY), SEVERITY)
        first = next_intake_step(state, [])
        state.update(first)

        again = next_intake_step(state, [])

        self.assertNotIn("I wasn't able to capture", again["response_text"])
        self.assertEqual(again["question_field_ids"], [])


class GoodbyeTests(unittest.TestCase):
    def test_goodbye_with_mandatory_questions_open_asks_only_those(self):
        form = form_with(*[i for i in MANDATORY_IDS if section_of(i) == "Present Complaint"])
        state = state_for(form, end_requested=True, completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertEqual(
            set(step["question_field_ids"]),
            {"Pain Assessment.Primary Location of Pain", SEVERITY, "Pain Assessment.Aggravating Factors"},
        )
        self.assertNotIn(RELIEVING, step["question_field_ids"])

    def test_goodbye_after_a_mandatory_question_was_closed_is_honoured(self):
        state = state_for(
            form_filled_except(SEVERITY),
            question_ask_counts={SEVERITY: MAX_TIMES_ASKED},
            end_requested=True,
            completion_percentage=50,
        )

        asked, step = end_and_confirm(state)

        self.assertEqual(asked["response_text"].count(END_CONFIRM), 1)
        self.assertEqual(step["phase"], "summary")
        self.assertEqual(step["form"]["Pain Assessment"]["Severity (1-10)"], "Not provided")

    def test_goodbye_while_a_mandatory_question_can_still_be_asked_is_not(self):
        state = state_for(
            form_filled_except(SEVERITY),
            question_ask_counts={SEVERITY: MAX_TIMES_ASKED - 1},
            end_requested=True,
            completion_percentage=0,
        )

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertEqual(step["question_field_ids"], [SEVERITY])

    def test_goodbye_below_the_completion_percentage_keeps_asking(self):
        state = state_for(form_with(*MANDATORY_IDS), end_requested=True, completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(
            END_NOT_YET_MORE.format(current="44", minimum="50")
        ))
        self.assertEqual(step["phase"], "interviewing")
        self.assertTrue(step["question_field_ids"])

    def test_goodbye_at_the_completion_percentage_is_honoured(self):
        form = form_with(*MANDATORY_IDS, RELIEVING)
        state = state_for(form, end_requested=True, completion_percentage=50)

        _, step = end_and_confirm(state)

        self.assertEqual(step["phase"], "summary")
        self.assertIn(SUMMARY_QUESTION, step["response_text"])
        self.assertEqual(step["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"], "Not provided")

    def test_a_zero_completion_percentage_only_needs_the_mandatory_answers(self):
        state = state_for(form_with(*MANDATORY_IDS), end_requested=True, completion_percentage=0)

        _, step = end_and_confirm(state)

        self.assertEqual(step["phase"], "summary")

    def test_without_a_goodbye_the_interview_is_not_cut_short(self):
        state = state_for(form_with(*MANDATORY_IDS, RELIEVING), completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertEqual(step["phase"], "interviewing")

    def test_the_goodbye_signal_does_not_carry_into_the_next_turn(self):
        state = state_for(form_with(*MANDATORY_IDS), end_requested=True, completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertFalse(step["end_requested"])


def with_additional_complaint(form: dict) -> dict:
    form["Additional Complaint 1"] = {
        "Primary Complaint": "Knee pain",
        "Duration of the Issue": "",
        "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "",
        "Severity (1-10)": "",
        "Aggravating Factors": "",
        "Relieving Factors": "",
    }
    return form


class AdditionalComplaintIsOptionalTests(unittest.TestCase):
    """An added complaint's questions are optional, so they can never trap the patient."""

    def test_unanswered_extra_complaint_questions_close_instead_of_blocking_the_summary(self):
        state = state_for(with_additional_complaint(form_filled_except()))
        shown = {}
        for _ in range(20):
            step = next_intake_step(state, [])
            if step["phase"] == "summary":
                break
            state.update(step)
            for field_id in step["question_field_ids"]:
                shown[field_id] = shown.get(field_id, 0) + 1
        else:
            self.fail("the interview never reached the summary")

        self.assertTrue(shown)
        self.assertLessEqual(max(shown.values()), MAX_TIMES_ASKED)
        self.assertTrue(all(section_of(field_id) == "Additional Complaint 1" for field_id in shown))
        self.assertEqual(step["form"]["Additional Complaint 1"]["Duration of the Issue"], "Not provided")

    def test_extra_complaint_questions_use_the_plain_retry_not_the_required_wording(self):
        form = with_additional_complaint(form_filled_except())
        first = next_intake_step(state_for(form), [])
        second = next_intake_step(state_for(form, question_ask_counts=first["question_ask_counts"]), [])

        self.assertTrue(second["response_text"].startswith(RETRY_NOTICE))
        self.assertNotIn(RETRY_MANDATORY_NOTICE, second["response_text"])

    def test_the_patient_can_end_once_the_main_intake_is_done_despite_an_open_extra_complaint(self):
        form = with_additional_complaint(form_with(*MANDATORY_IDS, RELIEVING))
        state = state_for(form, end_requested=True, completion_percentage=50)

        _, step = end_and_confirm(state)

        self.assertEqual(step["phase"], "summary")
        self.assertEqual(step["form"]["Additional Complaint 1"]["Duration of the Issue"], "Not provided")

    def test_open_extra_complaint_questions_do_not_count_against_the_percentage(self):
        form = with_additional_complaint(form_with(*MANDATORY_IDS, RELIEVING))

        status = completion_status(form, list(form), "specific_complaint", 50)

        self.assertEqual(status.mandatory_missing, [])
        self.assertEqual(status.percent, 50.0)
        self.assertTrue(status.can_finish)

    def test_the_main_complaint_still_blocks_ending_when_a_mandatory_answer_is_missing(self):
        form = with_additional_complaint(form_filled_except(SEVERITY))
        state = state_for(form, end_requested=True, completion_percentage=0)

        step = next_intake_step(state, [])

        self.assertNotEqual(step["phase"], "summary")
        self.assertEqual(step["question_field_ids"], [SEVERITY])


class EndNoticeTests(unittest.TestCase):
    """A patient who asks to end is told why not, and what it takes to finish."""

    def _ending(self, form, **extra) -> dict:
        state = state_for(form, end_requested=True, completion_percentage=50, **extra)
        return next_intake_step(state, [])

    def test_it_names_the_required_questions_and_the_percentage_still_to_reach(self):
        step = self._ending(load_form("FRM-01").empty_form())

        text = step["response_text"]
        self.assertTrue(text.startswith(END_NOT_YET_REQUIRED))
        self.assertIn("at least 50% of the form", text)
        self.assertIn("you're at 0% now", text)

    def test_it_does_not_mention_the_percentage_when_it_is_already_reached(self):
        form = form_filled_except(SEVERITY)

        step = self._ending(form)

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertNotIn("% of the form", step["response_text"])

    def test_it_does_not_mention_a_percentage_when_none_is_required(self):
        state = state_for(
            load_form("FRM-01").empty_form(), end_requested=True, completion_percentage=0
        )

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertNotIn("% of the form", step["response_text"])

    def test_with_the_mandatory_answers_in_it_says_how_far_the_form_still_has_to_go(self):
        step = self._ending(form_with(*MANDATORY_IDS))

        self.assertTrue(step["response_text"].startswith("We're at 44% and need at least 50%"))

    def test_the_goodbye_notice_outranks_the_refusal_notice(self):
        state = state_for(
            load_form("FRM-01").empty_form(),
            end_requested=True,
            mandatory_notice=True,
            completion_percentage=50,
        )

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertNotIn(MANDATORY_NOTICE, step["response_text"])

    def test_asking_to_end_over_and_over_lets_the_patient_go_after_three_asks(self):
        """The transcript: three goodbyes in a row ask the required questions three times, then release."""

        state = state_for(
            form_with(*[i for i in MANDATORY_IDS if section_of(i) == "Present Complaint"]),
            end_requested=True,
            completion_percentage=0,
        )
        notices = []
        for _ in range(3):
            step = next_intake_step(state, [])
            notices.append(step["response_text"].startswith(END_NOT_YET_REQUIRED))
            state.update(step)
            state["end_requested"] = True

        asked, final = end_and_confirm(state)

        self.assertEqual(notices, [True, True, True])
        self.assertIn(END_CONFIRM, asked["response_text"])
        self.assertEqual(final["phase"], "summary")
        self.assertEqual(final["form"]["Pain Assessment"]["Severity (1-10)"], "Not provided")


class EndConfirmationTests(unittest.TestCase):
    """A patient who has done the required questions is asked once more before the interview ends."""

    def _done_state(self, **extra) -> dict:
        return state_for(
            form_with(*MANDATORY_IDS, RELIEVING), end_requested=True, completion_percentage=50, **extra
        )

    def test_they_are_asked_to_confirm_and_nothing_is_closed_yet(self):
        step = next_intake_step(self._done_state(), [])

        self.assertEqual(step["response_text"], END_CONFIRM)
        self.assertEqual(step["phase"], "interviewing")
        self.assertEqual(step["question_field_ids"], [])
        self.assertTrue(step["history"][-1]["end_confirm"])
        self.assertEqual(step["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"], "")

    def test_yes_ends_the_interview_with_the_summary(self):
        _, final = end_and_confirm(self._done_state())

        self.assertEqual(final["phase"], "summary")
        self.assertIn(SUMMARY_QUESTION, final["response_text"])
        self.assertEqual(final["form"]["Treatment Goals"]["Short-Term Goals (within 3 months)"], "Not provided")

    def test_no_carries_on_without_an_apology(self):
        goals = ["Treatment Goals.Short-Term Goals (within 3 months)"]
        state = self._done_state(question_ask_counts={goals[0]: 1})
        state.update(end_requested=False, end_reply="declined")

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_CONTINUE))
        self.assertNotIn(RETRY_NOTICE, step["response_text"])
        self.assertTrue(step["question_field_ids"])
        self.assertEqual(step["phase"], "interviewing")

    def test_required_questions_still_open_get_persuasion_not_a_confirmation(self):
        state = state_for(load_form("FRM-01").empty_form(), end_requested=True, completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(END_NOT_YET_REQUIRED))
        self.assertIn("You haven't answered the required questions yet", step["response_text"])
        self.assertIn("needed for your treatment plan", step["response_text"])
        self.assertNotIn(END_CONFIRM, step["response_text"])
        self.assertTrue(step["question_field_ids"])

    def test_too_little_of_the_form_done_gets_persuasion_not_a_confirmation(self):
        state = state_for(form_with(*MANDATORY_IDS), end_requested=True, completion_percentage=50)

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith("We're at 44% and need at least 50%"))
        self.assertNotIn(END_CONFIRM, step["response_text"])

    def test_a_required_question_closed_on_this_turn_is_mentioned_with_the_confirmation(self):
        state = state_for(
            form_filled_except(SEVERITY),
            question_ask_counts={SEVERITY: MAX_TIMES_ASKED},
            end_requested=True,
            completion_percentage=50,
        )

        step = next_intake_step(state, [])

        self.assertTrue(step["response_text"].startswith(MANDATORY_LATER.format(labels="Severity")))
        self.assertTrue(step["response_text"].endswith(END_CONFIRM))

    def test_the_confirmation_signals_do_not_carry_into_the_next_turn(self):
        for state in (self._done_state(), self._done_state(end_reply="confirmed")):
            step = next_intake_step(state, [])

            self.assertFalse(step["end_requested"])
            self.assertEqual(step["end_reply"], "")


class CompletionStatusTests(unittest.TestCase):
    def test_all_mandatory_answered_and_enough_done_can_finish(self):
        status = completion_status(
            form_with(*MANDATORY_IDS, RELIEVING), list(load_form("FRM-01").sections), "specific_complaint", 50
        )

        self.assertEqual(status.mandatory_missing, [])
        self.assertTrue(status.can_finish)

    def test_a_missing_mandatory_answer_blocks_finishing_at_any_percentage(self):
        form = form_filled_except(SEVERITY)

        status = completion_status(form, list(form), "specific_complaint", 0)

        self.assertEqual(status.mandatory_missing, [("Pain Assessment", "Severity (1-10)")])
        self.assertFalse(status.can_finish)

    def test_a_mandatory_field_closed_after_three_asks_does_not_hold_the_patient_back(self):
        form = form_filled_except()
        form["Pain Assessment"]["Severity (1-10)"] = "Not provided"

        status = completion_status(form, list(form), "specific_complaint", 0)

        self.assertEqual(status.mandatory_missing, [])
        self.assertTrue(status.can_finish)

    def test_below_the_percentage_blocks_finishing(self):
        form = form_with(*MANDATORY_IDS)

        status = completion_status(form, list(form), "specific_complaint", 50)

        self.assertEqual(status.mandatory_missing, [])
        self.assertFalse(status.can_finish)

    def test_a_general_assessment_needs_no_complaint_fields(self):
        form = load_form("FRM-01").empty_form()

        status = completion_status(form, list(form), "general_assessment", 0)

        self.assertEqual(status.mandatory_missing, [])


class CompletionPercentageSettingTests(unittest.TestCase):
    def test_it_defaults_when_unset(self):
        with patch_env.dict(os.environ, {}, clear=False):
            os.environ.pop("COMPLETION_PERCENTAGE_TEST", None)
            self.assertEqual(_percent_env("COMPLETION_PERCENTAGE_TEST", 50), 50)

    def test_it_reads_the_environment(self):
        with patch_env.dict(os.environ, {"COMPLETION_PERCENTAGE_TEST": "62.5"}):
            self.assertEqual(_percent_env("COMPLETION_PERCENTAGE_TEST", 50), 62.5)

    def test_it_rejects_values_outside_zero_to_one_hundred(self):
        for bad in ("-1", "101", "abc"):
            with self.subTest(value=bad), patch_env.dict(os.environ, {"COMPLETION_PERCENTAGE_TEST": bad}):
                with self.assertRaises(ValueError):
                    _percent_env("COMPLETION_PERCENTAGE_TEST", 50)


if __name__ == "__main__":
    unittest.main()
