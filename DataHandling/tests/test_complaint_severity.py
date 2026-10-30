import unittest
import sys
import types

from src.graph.pure_functions.additional_complaint import capture_additional_complaint
from src.graph.pure_functions.complaint_severity import explicit_severity_updates
from src.graph.state import get_fresh_interview_state
from src.graph.nodes.correction import make_detect_correction_node, make_apply_correction_node
try:
    from src.graph.nodes.summary import make_classify_summary_intent_node
except ModuleNotFoundError as exc:
    if exc.name != "langgraph":
        raise
    config = types.ModuleType("langgraph.config")
    config.get_stream_writer = lambda: (lambda _event: None)
    sys.modules.setdefault("langgraph", types.ModuleType("langgraph"))
    sys.modules["langgraph.config"] = config
    from src.graph.nodes.summary import make_classify_summary_intent_node


class ComplaintSeverityTests(unittest.TestCase):
    def setUp(self):
        self.form = get_fresh_interview_state("u", "FRM-01", "s")["form"]
        self.form["Present Complaint"]["Primary Complaint"] = "Head pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "head"
        self.form["Pain Assessment"]["Severity (1-10)"] = "6/10"
        self.form, self.section = capture_additional_complaint(self.form, "I also have leg pain")

    def _severity_updates(self, text, active_section=None, question=None):
        return explicit_severity_updates(self.form, text, active_section, question)

    def test_two_scores_bind_to_correct_complaints_in_either_order(self):
        for text in ("Head pain is 10 out of 10 and leg pain is 7 out of 10",
                     "7 out of 10 for my leg, 10 out of 10 for my head",
                     "Leg pain is 7/10 but headache is 10/10"):
            self.assertEqual(self._severity_updates(text), {
                ("Pain Assessment", "Severity (1-10)"): "10/10",
                (self.section, "Severity (1-10)"): "7/10",
            })

    def test_unrelated_turn_has_no_rating(self):
        self.assertEqual(self._severity_updates("I heard about you from a friend"), {})

    def test_unlabelled_score_uses_active_complaint_only(self):
        self.assertEqual(
            self._severity_updates("7 out of 10", self.section),
            {(self.section, "Severity (1-10)"): "7/10"},
        )
        self.assertEqual(self._severity_updates("7/10"), {})

    def test_bare_score_uses_active_complaint_when_severity_was_asked(self):
        question = "On a scale of 0 to 10, how severe is it right now?"
        for text in ("5", "it is 5", "on 0 to 10 it is 5, movement makes it worse"):
            with self.subTest(text=text):
                self.assertEqual(
                    self._severity_updates(text, self.section, question),
                    {(self.section, "Severity (1-10)"): "5/10"},
                )

    def test_bare_number_without_severity_question_is_not_a_rating(self):
        self.assertEqual(
            self._severity_updates("5", self.section, "How many days have you had this?"),
            {},
        )

    def test_summary_update_applies_both_without_provider(self):
        def unexpected(_):
            self.fail("Explicit scoped score must not require AI")
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(form=self.form, phase="summary", user_input="Head is 10/10 and leg is 7/10")
        state.update(make_classify_summary_intent_node(unexpected)(state))
        self.assertEqual(state["summary_intent"]["intent"], "request_change")
        state.update(make_detect_correction_node(unexpected)(state))
        result = make_apply_correction_node(unexpected)(state)
        self.assertTrue(result["correction_applied"])
        self.assertEqual(result["form"][self.section]["Severity (1-10)"], "7/10")
        self.assertEqual(result["form"]["Pain Assessment"]["Severity (1-10)"], "10/10")
        self.assertNotIn("Key points", result["response_text"])

    def test_different_sites_not_specific_to_head_and_leg(self):
        self.form["Present Complaint"]["Primary Complaint"] = "Shoulder pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "shoulder"
        self.form[self.section]["Primary Complaint"] = "Also knee pain"
        self.assertEqual(self._severity_updates("Shoulder 4/10 and knee 8/10"), {
            ("Pain Assessment", "Severity (1-10)"): "4/10",
            (self.section, "Severity (1-10)"): "8/10",
        })

    def test_single_named_update_preserves_other_complaint(self):
        self.form[self.section]["Severity (1-10)"] = "7/10"
        self.assertEqual(
            self._severity_updates("Update my head pain to 10 out of 10"),
            {("Pain Assessment", "Severity (1-10)"): "10/10"},
        )

    def test_qa_primary_leg_correction_wins_over_additional_complaint(self):
        self.form["Present Complaint"]["Primary Complaint"] = "Leg pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "Leg"
        self.form["Pain Assessment"]["Severity (1-10)"] = "9/10"
        self.form[self.section]["Primary Complaint"] = "Head pain"
        self.form[self.section]["Severity (1-10)"] = "5/10"

        for text in (
            "pls update leg pain to 10 from 9",
            "please change leg pain 9→10",
            "update leg pain rating is 10",
        ):
            with self.subTest(text=text):
                updates = explicit_severity_updates(
                    self.form, text, "Pain Assessment"
                )
                self.assertEqual(
                    updates,
                    {("Pain Assessment", "Severity (1-10)"): "10/10"},
                )

    def test_qa_summary_leg_correction_does_not_rename_additional_complaint(self):
        from src.graph.nodes.summary import make_handle_summary_response_node

        self.form["Present Complaint"]["Primary Complaint"] = "Leg pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "Leg"
        self.form["Pain Assessment"]["Severity (1-10)"] = "9/10"
        self.form[self.section]["Primary Complaint"] = "Head pain"
        self.form[self.section]["Severity (1-10)"] = "5/10"
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(
            form=self.form,
            phase="summary",
            user_input="pls update leg pain to 10 from 9",
        )

        def unexpected(_):
            self.fail("Explicit severity correction must not call AI")

        state.update(make_classify_summary_intent_node(unexpected)(state))
        self.assertEqual(state["summary_intent"]["intent"], "request_change")
        state.update(make_detect_correction_node(unexpected)(state))
        result = make_apply_correction_node(unexpected)(state)
        self.assertEqual(
            result["form"]["Pain Assessment"]["Severity (1-10)"], "10/10"
        )
        self.assertEqual(
            result["form"][self.section]["Primary Complaint"], "Head pain"
        )
        self.assertEqual(result["form"][self.section]["Severity (1-10)"], "5/10")

    def test_side_is_required_for_two_knee_complaints(self):
        self.form["Present Complaint"]["Primary Complaint"] = "Left knee pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "left knee"
        self.form[self.section]["Primary Complaint"] = "Right knee pain"
        self.assertEqual(explicit_severity_updates(self.form, "Knee is 7/10"), {})
        self.assertEqual(self._severity_updates("Left knee 3/10 and right knee 7/10"), {
            ("Pain Assessment", "Severity (1-10)"): "3/10",
            (self.section, "Severity (1-10)"): "7/10",
        })
