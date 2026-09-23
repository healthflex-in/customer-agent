import copy
import unittest
import sys
import types

from src.graph.pure_functions.additional_complaint import capture_additional_complaint
from src.graph.pure_functions.complaint_severity import explicit_severity_updates, reconcile_severities
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

    def test_two_scores_bind_to_correct_complaints_in_either_order(self):
        for text in ("Head pain is 10 out of 10 and leg pain is 7 out of 10",
                     "7 out of 10 for my leg, 10 out of 10 for my head",
                     "Leg pain is 7/10 but headache is 10/10"):
            result = reconcile_severities(self.form, self.form, text)
            self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "10/10")
            self.assertEqual(result[self.section]["Severity (1-10)"], "7/10")

    def test_model_combined_severity_is_rejected(self):
        contaminated = copy.deepcopy(self.form)
        contaminated[self.section]["Severity (1-10)"] = "Head 10/10 and leg 7/10"
        result = reconcile_severities(self.form, contaminated, "Head is 10/10 and leg is 7/10")
        self.assertEqual(result[self.section]["Severity (1-10)"], "7/10")

    def test_unrelated_turn_preserves_existing_ratings(self):
        contaminated = copy.deepcopy(self.form)
        contaminated[self.section]["Severity (1-10)"] = "10/10"
        result = reconcile_severities(self.form, contaminated, "I heard about you from a friend")
        self.assertEqual(result[self.section]["Severity (1-10)"], "")
        self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "6/10")

    def test_unlabelled_score_uses_active_complaint_only(self):
        result = reconcile_severities(self.form, self.form, "7 out of 10", self.section)
        self.assertEqual(result[self.section]["Severity (1-10)"], "7/10")
        self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "6/10")
        self.assertEqual(explicit_severity_updates(self.form, "7/10"), {})

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
        result = reconcile_severities(self.form, self.form, "Shoulder 4/10 and knee 8/10")
        self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "4/10")
        self.assertEqual(result[self.section]["Severity (1-10)"], "8/10")

    def test_single_named_update_preserves_other_complaint(self):
        self.form[self.section]["Severity (1-10)"] = "7/10"
        result = reconcile_severities(self.form, self.form, "Update my head pain to 10 out of 10")
        self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "10/10")
        self.assertEqual(result[self.section]["Severity (1-10)"], "7/10")

    def test_side_is_required_for_two_knee_complaints(self):
        self.form["Present Complaint"]["Primary Complaint"] = "Left knee pain"
        self.form["Pain Assessment"]["Primary Location of Pain"] = "left knee"
        self.form[self.section]["Primary Complaint"] = "Right knee pain"
        self.assertEqual(explicit_severity_updates(self.form, "Knee is 7/10"), {})
        result = reconcile_severities(self.form, self.form, "Left knee 3/10 and right knee 7/10")
        self.assertEqual(result["Pain Assessment"]["Severity (1-10)"], "3/10")
        self.assertEqual(result[self.section]["Severity (1-10)"], "7/10")
