import json
import sys
import types
import unittest

from src.forms.loader import load_form
from src.graph.pure_functions.answer_mapping import (
    NOT_APPLICABLE,
    NOT_PROVIDED,
    map_reply,
)
from src.graph.pure_functions.form_validation import get_all_missing_fields
from src.graph.pure_functions.lifestyle import lifestyle_component_value
from src.graph.pure_functions.summary import SUMMARY_QUESTION, classify_summary_response
from src.graph.state import get_fresh_interview_state

try:
    from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    langgraph_config_module = types.ModuleType("langgraph.config")
    langgraph_config_module.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
    sys.modules["langgraph.config"] = langgraph_config_module
    from src.graph.nodes.generate import MAX_TIMES_ASKED, next_intake_step


CONSULTED = "Previous Consultations.Previous Diagnosis or Advice and Prescribed Treatment Taken"
STATUS = "Previous Consultations.Current Status of Issue (Improved, Same, Worse)"


def answer(field_id, value, status="answered", correction=False, detail=""):
    return {
        "field_id": field_id, "status": status, "value": value,
        "detail": detail, "correction": correction,
    }


def mapped(form, answers, visit_context="specific_complaint", proposed="specific_complaint"):
    payload = json.dumps({
        "visit_context": proposed,
        "answers": answers,
        "reports": {"has_reports": None, "wants_upload": None},
    })
    return map_reply(
        reply="patient reply",
        last_question="Sage question",
        form=form,
        form_sections=list(form),
        visit_context=visit_context,
        llm=lambda _prompt: payload,
    )


def empty_form():
    return load_form("FRM-01").empty_form()


class AnswerMappingTests(unittest.TestCase):
    def test_mixed_order_answers_map_to_every_field_including_unasked(self):
        result = mapped(empty_form(), [
            answer("Pain Assessment.Relieving Factors", "Rest"),
            answer("Present Complaint.Duration of the Issue", "Three weeks"),
            answer("Treatment Goals.Long-Term Goals (after 3 months)", "Run a marathon"),
            answer("Pain Assessment.Aggravating Factors", "Climbing stairs"),
        ])

        self.assertEqual(result.form["Pain Assessment"]["Relieving Factors"], "Rest")
        self.assertEqual(result.form["Pain Assessment"]["Aggravating Factors"], "Climbing stairs")
        self.assertEqual(result.form["Present Complaint"]["Duration of the Issue"], "Three weeks")
        self.assertEqual(
            result.form["Treatment Goals"]["Long-Term Goals (after 3 months)"], "Run a marathon"
        )
        self.assertEqual(len(result.updated_field_ids), 4)

    def test_denied_parent_settles_its_dependent_as_not_applicable(self):
        result = mapped(empty_form(), [
            answer(STATUS, "Worse"),
            answer(CONSULTED, "Has not consulted anyone", status="denied"),
        ])

        consultations = result.form["Previous Consultations"]
        self.assertEqual(consultations["Current Status of Issue (Improved, Same, Worse)"], NOT_APPLICABLE)
        self.assertNotIn(
            "Previous Consultations",
            get_all_missing_fields(result.form, ["Previous Consultations"]),
        )

    def test_answered_parent_leaves_dependent_to_be_asked(self):
        result = mapped(empty_form(), [answer(CONSULTED, "Physio advised stretching")])

        self.assertEqual(
            get_all_missing_fields(result.form, ["Previous Consultations"]),
            {"Previous Consultations": ["Current Status of Issue (Improved, Same, Worse)"]},
        )

    def test_parent_correction_settles_and_reopens_the_dependent(self):
        answered = mapped(empty_form(), [
            answer(CONSULTED, "Physio advised stretching"),
            answer(STATUS, "Same"),
        ]).form
        denied = mapped(answered, [
            answer(CONSULTED, "Never consulted anyone", status="denied", correction=True),
        ]).form
        reopened = mapped(denied, [
            answer(CONSULTED, "Saw an orthopaedic doctor", correction=True),
        ]).form

        status_field = "Current Status of Issue (Improved, Same, Worse)"
        self.assertEqual(denied["Previous Consultations"][status_field], NOT_APPLICABLE)
        self.assertEqual(reopened["Previous Consultations"][status_field], "")

    def test_values_follow_the_declared_field_rules(self):
        result = mapped(empty_form(), [
            answer("Pain Assessment.Severity (1-10)", "8"),
            answer("Present Complaint.Onset (Gradual or Sudden)", "sudden"),
        ])
        rejected = mapped(empty_form(), [
            answer("Pain Assessment.Severity (1-10)", "12"),
            answer("Present Complaint.Onset (Gradual or Sudden)", "after a fall"),
        ])

        self.assertEqual(result.form["Pain Assessment"]["Severity (1-10)"], "8/10")
        self.assertEqual(result.form["Present Complaint"]["Onset (Gradual or Sudden)"], "Sudden")
        self.assertEqual(rejected.form["Pain Assessment"]["Severity (1-10)"], "")
        self.assertEqual(rejected.form["Present Complaint"]["Onset (Gradual or Sudden)"], "")

    def test_fixed_value_keeps_the_patient_detail(self):
        consulted = mapped(empty_form(), [answer(CONSULTED, "Physio advised core exercises")]).form
        result = mapped(consulted, [
            answer(
                STATUS, "Same",
                detail="Exercises helped temporarily; pain returns after prolonged sitting",
            ),
            answer("Present Complaint.Onset (Gradual or Sudden)", "Gradual"),
        ])

        self.assertEqual(
            result.form["Previous Consultations"]["Current Status of Issue (Improved, Same, Worse)"],
            "Same — Exercises helped temporarily; pain returns after prolonged sitting",
        )
        self.assertEqual(result.form["Present Complaint"]["Onset (Gradual or Sudden)"], "Gradual")

    def test_unsure_and_denied_keep_the_model_statement(self):
        result = mapped(empty_form(), [
            answer("Present Complaint.Mechanism of Injury or Cause", "Does not remember", status="unsure"),
            answer("Pain Assessment.Relieving Factors", "Nothing helps", status="denied"),
        ])

        self.assertEqual(
            result.form["Present Complaint"]["Mechanism of Injury or Cause"], "Does not remember"
        )
        self.assertEqual(result.form["Pain Assessment"]["Relieving Factors"], "Nothing helps")

    def test_answered_field_changes_only_on_an_explicit_correction(self):
        form = empty_form()
        form["Pain Assessment"]["Primary Location of Pain"] = "Left knee"

        kept = mapped(form, [answer("Pain Assessment.Primary Location of Pain", "Right knee")])
        corrected = mapped(form, [
            answer("Pain Assessment.Primary Location of Pain", "Right knee", correction=True),
        ])

        self.assertEqual(kept.form["Pain Assessment"]["Primary Location of Pain"], "Left knee")
        self.assertEqual(corrected.form["Pain Assessment"]["Primary Location of Pain"], "Right knee")

    def test_lifestyle_components_map_independently(self):
        result = mapped(empty_form(), [
            answer("History & Diagnostics.Current Lifestyle — Activity or Exercise", "Swimming"),
            answer("History & Diagnostics.Current Lifestyle — Work", "Teacher"),
        ])

        lifestyle = result.form["History & Diagnostics"]["Current Lifestyle"]
        self.assertEqual(lifestyle_component_value(lifestyle, "work"), "Teacher")
        self.assertEqual(lifestyle_component_value(lifestyle, "activity"), "Swimming")

    def test_model_answers_by_short_field_key(self):
        prompts = []
        form = empty_form()

        def model(prompt):
            prompts.append(prompt)
            key = next(
                line.split(":", 1)[0].removeprefix("- ")
                for line in prompt.splitlines()
                if "— Long-Term Goals (after 3 months) —" in line
            )
            return json.dumps({"answers": [
                answer(key, "Return to running"),
                answer("Treatment Goals.Long-Term Goals (after 3 months):", "Copied name"),
            ]})

        result = map_reply(
            reply="I want to run again", last_question="", form=form,
            form_sections=list(form), visit_context="specific_complaint", llm=model,
        )

        self.assertEqual(
            result.form["Treatment Goals"]["Long-Term Goals (after 3 months)"], "Return to running"
        )
        self.assertEqual(result.updated_field_ids, ["Treatment Goals.Long-Term Goals (after 3 months)"])

    def test_unreadable_model_output_leaves_the_form_unchanged(self):
        form = empty_form()
        result = map_reply(
            reply="hello", last_question="", form=form, form_sections=list(form),
            visit_context="unknown", llm=lambda _prompt: "not json",
        )

        self.assertEqual(result.form, form)
        self.assertEqual(result.updated_field_ids, [])

    def test_visit_type_skips_and_reopens_sections(self):
        general = mapped(empty_form(), [], visit_context="unknown", proposed="general_assessment")
        specific = mapped(general.form, [], visit_context="general_assessment")

        for section in load_form("FRM-01").visit_types["general_assessment"].skip_sections:
            self.assertTrue(all(v == NOT_APPLICABLE for v in general.form[section].values()))
            self.assertTrue(all(v == "" for v in specific.form[section].values()))
        self.assertEqual(specific.visit_context, "specific_complaint")

    def test_prompt_lists_rules_from_the_form_definition(self):
        prompts = []
        form = empty_form()
        map_reply(
            reply="hi", last_question="", form=form, form_sections=list(form),
            visit_context="unknown", llm=lambda prompt: prompts.append(prompt) or "{}",
        )

        self.assertIn("Onset (Gradual or Sudden): one of Gradual, Sudden", prompts[0])
        self.assertIn(
            "one of Improved, Same, Worse. It means: How the issue is now, "
            "compared with before the previous treatment",
            prompts[0],
        )
        self.assertIn("Severity (1-10): a whole number from 0 to 10", prompts[0])
        self.assertIn(
            "only applies when the patient answered "
            "Previous Diagnosis or Advice and Prescribed Treatment Taken",
            prompts[0],
        )


class IntakeStepTests(unittest.TestCase):
    def state_missing(self, field_id, times_asked):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        for fields in state["form"].values():
            for name in fields:
                fields[name] = "Provided"
        state["form"]["History & Diagnostics"]["Current Lifestyle"] = (
            "Work: Teacher; Activity/exercise: Swimming"
        )
        section, name = field_id.split(".", 1)
        state["form"][section][name] = ""
        state["visit_context"] = "specific_complaint"
        state["question_ask_counts"] = {field_id: times_asked}
        return state

    def test_question_is_asked_again_until_the_limit(self):
        field_id = "Pain Assessment.Relieving Factors"
        step = next_intake_step(self.state_missing(field_id, MAX_TIMES_ASKED - 1), [])

        self.assertEqual(step["phase"], "interviewing")
        self.assertEqual(step["question_ask_counts"][field_id], MAX_TIMES_ASKED)

    def test_question_at_the_limit_is_saved_as_not_provided_and_summary_follows(self):
        field_id = "Pain Assessment.Relieving Factors"
        step = next_intake_step(self.state_missing(field_id, MAX_TIMES_ASKED), [])

        self.assertEqual(step["phase"], "summary")
        self.assertEqual(step["form"]["Pain Assessment"]["Relieving Factors"], NOT_PROVIDED)
        self.assertTrue(step["response_text"].endswith(SUMMARY_QUESTION))

    def test_parent_saved_as_not_provided_does_not_trigger_its_dependent(self):
        state = self.state_missing(CONSULTED, MAX_TIMES_ASKED)
        state["form"]["Previous Consultations"]["Current Status of Issue (Improved, Same, Worse)"] = ""

        step = next_intake_step(state, [])

        self.assertEqual(step["phase"], "summary")
        self.assertEqual(
            step["form"]["Previous Consultations"]["Current Status of Issue (Improved, Same, Worse)"],
            NOT_APPLICABLE,
        )


class SummaryReplyTests(unittest.TestCase):
    def classify(self, text):
        return classify_summary_response(
            text, "summary", lambda _prompt: self.fail("Clear replies must not call the model")
        )

    def test_no_confirms_the_summary(self):
        self.assertEqual(self.classify("no")["intent"], "confirm")

    def test_yes_asks_what_to_change(self):
        result = self.classify("yes")
        self.assertEqual(result["intent"], "request_change")
        self.assertIsNone(result["correction_text"])


if __name__ == "__main__":
    unittest.main()
