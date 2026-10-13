import unittest

from src.graph.state import get_fresh_interview_state
from src.graph.nodes.summary import make_handle_summary_response_node
from src.graph.pure_functions.summary import classify_summary_response, generate_interview_summary, reports_with_verified_upload


class AdditionalReportTests(unittest.TestCase):
    def classify(self, text):
        return classify_summary_response(text, "summary", lambda _: self.fail("Unexpected AI call"))

    def handle(self, text, uploaded=True):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(phase="summary", reports_uploaded=uploaded, user_input=text,
                     summary_intent=self.classify(text))
        state["form"]["History & Diagnostics"]["Reports"] = "No relevant diagnostic reports available"
        return make_handle_summary_response_node(lambda _: self.fail("Unexpected summary"))(state)

    def test_additional_reports_allow_upload_with_existing_attachments(self):
        for text in ("i have other report", "i said i have other report",
                     "I uploaded my X-ray and have another report", "I have an additional MRI report"):
            with self.subTest(text=text):
                result = self.handle(text)
                self.assertTrue(result["request_attachment"])
                self.assertTrue(result["awaiting_report_upload"])
                self.assertIn("remain attached", result["response_text"])
                self.assertNotIn("no need", result["response_text"].lower())
                self.assertEqual(result.get("phase", "summary"), "summary")

    def test_upload_acknowledgement_is_concise_and_updates_reports(self):
        for text in ("I have uploaded my clinical documents.", "already attach means i uloaded just now"):
            result = self.handle(text)
            self.assertFalse(result["request_attachment"])
            self.assertIn("just uploaded", result["response_text"])
            self.assertNotIn("Key points", result["response_text"])
            self.assertNotIn("No relevant", result["form"]["History & Diagnostics"]["Reports"])

    def test_unverified_claim_does_not_fake_receipt(self):
        result = self.handle("I uploaded my report", uploaded=False)
        self.assertIn("cannot verify", result["response_text"])
        self.assertFalse(result.get("reports_uploaded", False))

    def test_uploaded_fact_visible_even_when_model_omits_it(self):
        form = {"History & Diagnostics": {"Reports": "X-ray uploaded and attached"}}
        result = generate_interview_summary(form, [], lambda _: "Back pain. Is this information correct, or would you like to make any changes?")
        self.assertIn("X-ray uploaded and attached", result)
        self.assertEqual(result.count("Is this information correct"), 1)

    def test_no_reports_response_is_not_empty(self):
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(summary_intent={"intent": "no_reports"}, reports_uploaded=True)
        result = make_handle_summary_response_node(lambda _: "unused")(state)
        self.assertTrue(result["response_text"])
        self.assertIn("uploaded", result["form"]["History & Diagnostics"]["Reports"])

    def test_negative_findings_are_preserved(self):
        self.assertIn("No fracture on X-ray", reports_with_verified_upload("No fracture on X-ray"))
        self.assertEqual(reports_with_verified_upload(reports_with_verified_upload("X-ray normal")), reports_with_verified_upload("X-ray normal"))

    def test_additional_report_upload_decline_does_not_prompt(self):
        result = self.handle("I have another report but I don't want to upload it")
        self.assertFalse(result["request_attachment"])
        self.assertIn("remain attached", result["response_text"])

    def test_upload_node_cannot_invent_receipt(self):
        from src.graph.nodes.upload import make_handle_upload_response_node
        state = get_fresh_interview_state("u", "FRM-01", "s")
        state.update(awaiting_report_upload=True, reports_uploaded=False,
                     user_input="I have uploaded my clinical documents")
        result = make_handle_upload_response_node()(state)
        self.assertFalse(result["reports_uploaded"])
        self.assertTrue(result["request_attachment"])


if __name__ == "__main__":
    unittest.main()
