"""Varied wording for the bot's acknowledgements, a tone read from the patient, and fixed clinical notices."""

import itertools
import json
import unittest

from src.graph.nodes.generate import next_intake_step
from src.graph.pure_functions.answer_mapping import map_reply
from src.graph.pure_functions.phrasing import (
    KNOWN_TONES,
    TONE_LEADS,
    VARIANTS,
    first_word,
    normalise_tone,
    pick,
    tone_lead,
)
from src.graph.pure_functions.question_plan import MANDATORY_NOTICE
from src.forms.loader import load_form


class VariantTests(unittest.TestCase):
    def test_each_situation_has_more_than_one_way_to_say_it(self):
        for key, options in VARIANTS.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(len(options), 2)
                self.assertEqual(len(set(options)), len(options))

    def test_the_same_turn_always_gets_the_same_variant(self):
        self.assertEqual(pick("noted", 4), pick("noted", 4))

    def test_consecutive_turns_do_not_repeat_the_same_sentence(self):
        for key, options in VARIANTS.items():
            with self.subTest(key=key):
                picked = [pick(key, turn) for turn in range(len(options))]
                self.assertEqual(len(set(picked)), len(options))

    def test_every_tone_lead_is_only_for_a_real_tone(self):
        self.assertEqual(tone_lead("neutral", 3), "")
        self.assertEqual(tone_lead("positive", 3), "")
        self.assertEqual(tone_lead(None, 3), "")
        for tone in ("frustrated", "confused", "distressed"):
            with self.subTest(tone=tone):
                self.assertIn(tone_lead(tone, 0), TONE_LEADS[tone])

    def test_an_unknown_tone_becomes_neutral(self):
        self.assertEqual(normalise_tone("angry"), "neutral")
        self.assertEqual(normalise_tone(None), "neutral")
        self.assertEqual(normalise_tone(" Frustrated "), "frustrated")
        self.assertTrue(set(TONE_LEADS) <= KNOWN_TONES)


class TemplateGrammarTests(unittest.TestCase):
    """A template that fills in a phrase must read correctly with every phrase."""

    FRAMES = ("I've noted {things}", "I've written down {things}", "that tells me {things}")

    def test_the_saved_templates_use_only_frames_that_suit_any_phrase(self):
        for template in VARIANTS["saved"]:
            with self.subTest(template=template):
                self.assertTrue(any(frame in template for frame in self.FRAMES), template)
                self.assertNotIn("I have {things}", template)

    def test_every_template_renders_cleanly_with_every_phrase(self):
        form = load_form("FRM-01")
        phrases = list(form.ack_phrases.values())
        self.assertGreater(len(phrases), 10)
        for template in VARIANTS["saved"]:
            for phrase in phrases:
                sentence = template.format(things=phrase)
                with self.subTest(sentence=sentence):
                    self.assertNotIn("{", sentence)
                    self.assertNotIn("  ", sentence)
                    self.assertTrue(sentence.endswith("."))
                    self.assertNotIn("now.", sentence)


class NoRepeatedOpeningTests(unittest.TestCase):
    """Two sentences in a row never start with the same word."""

    def test_a_variant_avoids_the_word_the_next_sentence_starts_with(self):
        for key, options in VARIANTS.items():
            for following in {first_word(option) for option in options}:
                for turn in range(len(options) + 1):
                    with self.subTest(key=key, following=following, turn=turn):
                        picked = pick(key, turn, avoid=f"{following.title()}, and so on.")
                        self.assertNotEqual(first_word(picked), following)

    def test_the_reported_case_okay_then_okay_is_gone(self):
        """An update note that can open with "Okay" next to the "Okay, let's dive deeper" introduction."""

        form = load_form("FRM-01").empty_form()
        intro = load_form("FRM-01").section_intro("Present Complaint")
        self.assertEqual(first_word(intro), "okay")
        self.assertTrue(any(first_word(line) == "okay" for line in VARIANTS["revision"]))

        for turn in range(len(VARIANTS["revision"]) * 2):
            with self.subTest(turn=turn):
                state = {
                    "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
                    "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": False,
                    "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
                    "noted_complaints": [], "saved_answers": [], "saved_field_ids": [],
                    "revised_answers": [{"field": "Severity (1-10)", "value": "6/10"}],
                    "patient_tone": "neutral",
                }
                history = [{"role": "agent", "message": "m"}] * turn

                text = next_intake_step(state, history)["response_text"]
                paragraphs = text.split("\n\n")

                self.assertEqual(paragraphs[1].split("\n")[0], intro)
                self.assertNotEqual(first_word(paragraphs[0]), "okay")

    def test_no_combination_of_acknowledgements_repeats_an_opening_word(self):
        for case, saved, revised, noted, decline, tone, turn in itertools.product(
            ("opening", "leftover"), (0, 1), (0, 1), (0, 1), (0, 1),
            ("neutral", "frustrated", "confused", "distressed"), range(6),
        ):
            form = load_form("FRM-01").empty_form()
            history = [{"role": "agent", "message": "m"}] * turn
            if case == "leftover":
                for name in ("Primary Complaint", "Duration of the Issue"):
                    form["Present Complaint"][name] = "x"
                history = history + [{
                    "role": "agent", "message": "b",
                    "question_field_ids": [f"Present Complaint.{name}" for name in form["Present Complaint"]],
                }]
            state = {
                "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
                "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": bool(decline),
                "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
                "noted_complaints": ["knee pain"] if noted else [],
                "revised_answers": [{"field": "Severity (1-10)", "value": "6/10"}] if revised else [],
                "saved_answers": ["Duration of the Issue"] if saved else [], "patient_tone": tone,
            }

            text = next_intake_step(state, history)["response_text"]
            paragraphs = [p for p in text.split("\n\n") if p.strip()]

            for before, after in zip(paragraphs, paragraphs[1:]):
                with self.subTest(case=case, saved=saved, revised=revised, noted=noted,
                                  decline=decline, tone=tone, turn=turn):
                    self.assertNotEqual(first_word(before), first_word(after), (before, after))


class ToneFromMappingTests(unittest.TestCase):
    def _mapping(self, tone):
        payload = json.dumps({
            "visit_context": "specific_complaint", "answers": [],
            "reports": {"has_reports": None, "wants_upload": None},
            "complaints": [], "tone": tone,
        })
        form = load_form("FRM-01").empty_form()
        return map_reply(
            reply="this is so long", last_question="", form=form, form_sections=list(form),
            visit_context="specific_complaint", llm=lambda _prompt: payload,
        )

    def test_the_mapping_reports_the_tone_it_read(self):
        self.assertEqual(self._mapping("frustrated").tone, "frustrated")

    def test_a_missing_or_unknown_tone_is_neutral(self):
        self.assertEqual(self._mapping("cheerful").tone, "neutral")

    def test_the_prompt_asks_for_the_tone(self):
        prompts = []
        form = load_form("FRM-01").empty_form()
        map_reply(
            reply="hi", last_question="", form=form, form_sections=list(form),
            visit_context="unknown", llm=lambda prompt: prompts.append(prompt) or "{}",
        )

        self.assertIn('"frustrated" when they are annoyed', prompts[0])
        self.assertIn('"tone": "neutral"', prompts[0])


class ReplyWordingTests(unittest.TestCase):
    def _state(self, **extra):
        form = load_form("FRM-01").empty_form()
        for fields in form.values():
            for name in fields:
                fields[name] = "Provided"
        form["Pain Assessment"]["Severity (1-10)"] = ""
        return {
            "form": form, "form_sections": list(form), "visit_context": "specific_complaint",
            "question_ask_counts": {}, "tagged_form_template": None, "decline_notice": False,
            "mandatory_notice": False, "end_requested": False, "completion_percentage": 0,
            "noted_complaints": [], "revised_answers": [], "saved_answers": [],
            "patient_tone": "neutral", **extra,
        }

    def test_a_frustrated_patient_gets_an_empathetic_opening_before_the_questions(self):
        step = next_intake_step(self._state(patient_tone="frustrated"), [{"role": "agent", "message": "x"}])

        self.assertTrue(step["response_text"].startswith(tone_lead("frustrated", 1)))
        self.assertIn("how severe", step["response_text"])

    def test_a_neutral_patient_gets_no_extra_opening(self):
        step = next_intake_step(self._state(patient_tone="neutral"), [{"role": "agent", "message": "x"}])

        for lead in (line for options in TONE_LEADS.values() for line in options):
            self.assertNotIn(lead, step["response_text"])

    def _leftover_state(self, **extra):
        """Present Complaint half answered; the open one was asked once before."""
        state = self._state(**extra)
        form = state["form"]
        form["Present Complaint"]["Mechanism of Injury or Cause"] = ""
        form["Pain Assessment"]["Severity (1-10)"] = "Provided"
        state["question_ask_counts"] = {"Present Complaint.Mechanism of Injury or Cause": 1}
        if "saved_field_ids" not in extra:
            # What was saved, as full ids, so the bot can tell a partial answer from a full one.
            sections = load_form("FRM-01").sections
            state["saved_field_ids"] = [
                f"{next((s for s, names in sections.items() if name in names), 'Present Complaint')}.{name}"
                for name in state["saved_answers"]
            ]
        return state

    def test_a_reply_that_saved_something_is_thanked_not_apologised_to(self):
        state = self._leftover_state(saved_answers=["Duration of the Issue"])

        step = next_intake_step(state, self._history())

        named = [line.format(things="how long it's been going on") for line in VARIANTS["saved"]]
        self.assertTrue(any(step["response_text"].startswith(line) for line in named))
        self.assertNotIn("Sorry", step["response_text"])
        self.assertNotIn("didn't catch", step["response_text"])
        self.assertIn("What caused it", step["response_text"])

    def test_a_reply_that_saved_nothing_still_gets_the_apology(self):
        state = self._leftover_state(saved_answers=[])

        step = next_intake_step(state, [{"role": "agent", "message": "x"}])

        self.assertIn("This one is required to complete your assessment", step["response_text"])

    def test_an_update_is_acknowledged_once_not_also_as_a_new_answer(self):
        state = self._leftover_state(
            saved_answers=["Severity (1-10)"],
            revised_answers=[{"field": "Severity (1-10)", "value": "6/10"}],
        )

        step = next_intake_step(state, [{"role": "agent", "message": "x"}])

        self.assertIn("Severity to 6/10", step["response_text"])
        self.assertNotIn("I've noted", step["response_text"])
        self.assertNotIn("I've written down", step["response_text"])

    def _history(self):
        """The follow-up case: the open question was just asked, in the same section."""
        return [{
            "role": "agent", "message": "earlier batch",
            "question_field_ids": ["Present Complaint.Mechanism of Injury or Cause"],
        }]

    def test_a_noted_complaint_is_the_only_acknowledgement_that_turn(self):
        """The reported case: a knee noted, and a detail saved in the same reply."""

        state = self._leftover_state(
            noted_complaints=["sharp pain in left knee"], saved_answers=["Mechanism of Injury or Cause"]
        )

        step = next_intake_step(state, self._history())

        paragraphs = step["response_text"].split("\n\n")
        self.assertIn("sharp pain in left knee", paragraphs[0])
        self.assertEqual(len(paragraphs), 2, "the noted line, then the questions, nothing in between")
        self.assertNotIn("helpful", step["response_text"])

    def test_an_accepted_skip_is_the_only_acknowledgement_that_turn(self):
        state = self._leftover_state(decline_notice=True, saved_answers=["Duration of the Issue"])

        step = next_intake_step(state, self._history())

        self.assertNotIn("I've noted", step["response_text"])
        self.assertNotIn("written down", step["response_text"])

    def test_what_was_saved_is_named_in_the_forms_own_words(self):
        state = self._leftover_state(saved_answers=["Duration of the Issue", "Onset (Gradual or Sudden)"])

        step = next_intake_step(state, self._history())

        self.assertIn("how long it's been going on and how it started", step["response_text"])

    def test_at_most_three_things_are_named_and_in_a_readable_list(self):
        state = self._leftover_state(saved_answers=[
            "Duration of the Issue", "Onset (Gradual or Sudden)", "Primary Location of Pain", "Severity (1-10)",
        ])

        step = next_intake_step(state, self._history())

        self.assertIn(
            "how long it's been going on, how it started and where it hurts", step["response_text"]
        )
        self.assertNotIn("how strong the pain is", step["response_text"])

    def test_a_field_with_no_phrase_is_saved_without_a_vague_line(self):
        state = self._leftover_state(saved_answers=["Some Field The Form Does Not Describe"])

        step = next_intake_step(state, self._history())

        self.assertFalse(step["response_text"].startswith(("Thanks", "Got it", "That's useful", "Okay")))
        self.assertNotIn("Sorry", step["response_text"], "a reply that saved something is not apologised to")

    def test_no_reply_ever_says_the_vague_thats_helpful_line(self):
        for saved in (["Duration of the Issue"], ["Primary Complaint"], []):
            for turn in range(6):
                state = self._leftover_state(saved_answers=saved)
                step = next_intake_step(state, self._history() * (turn + 1))
                self.assertNotIn("that's helpful", step["response_text"].lower())

    def test_every_form_field_has_a_plain_words_phrase(self):
        form = load_form("FRM-01")
        names = [name for fields in form.sections.values() for name in fields]
        names += ["Current Lifestyle — Work", "Current Lifestyle — Activity or Exercise"]

        self.assertEqual([name for name in names if not form.ack_phrase(name)], [])

    def test_the_mandatory_notice_is_never_varied_by_tone(self):
        state = self._state(patient_tone="frustrated", mandatory_notice=True)

        step = next_intake_step(state, [{"role": "agent", "message": "x"}])

        self.assertIn(MANDATORY_NOTICE, step["response_text"])


if __name__ == "__main__":
    unittest.main()
