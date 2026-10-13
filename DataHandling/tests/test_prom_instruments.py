import unittest

from app.forms.instruments import (
    apply_prom_answers,
    build_prom_snapshot,
    is_immutable_prom_question,
    questions_from_prom_snapshot,
)


QUESTIONS = [
    {
        "question_id": "prom_phq9_interest",
        "text": "Little interest or pleasure in doing things over the last 2 weeks?",
        "type": "single_choice",
        "options": ["Not at all", "Several days", "More than half the days", "Nearly every day"],
        "instrument_version": "PHQ-9 English",
    },
    {
        "question_id": "prom_phq9_item_9",
        "text": "Thoughts that you would be better off dead, or of hurting yourself?",
        "type": "single_choice",
        "options": ["Not at all", "Several days", "More than half the days", "Nearly every day"],
        "instrument_version": "PHQ-9 English",
    },
]


class PromInstrumentTests(unittest.TestCase):
    def test_prom_questions_are_immutable_but_business_questions_are_not(self):
        self.assertTrue(is_immutable_prom_question(QUESTIONS[0]))
        self.assertFalse(is_immutable_prom_question({"question_id": "biz_feedback_nps"}))

    def test_snapshot_separates_definition_from_stable_id_responses(self):
        snapshot = build_prom_snapshot(QUESTIONS, source_form_id="FRM-02")
        instrument = snapshot["instruments"][0]
        self.assertEqual(instrument["instrumentId"], "phq9")
        self.assertEqual(instrument["declaredVersion"], "PHQ-9 English")
        self.assertTrue(instrument["definitionHash"].startswith("sha256:"))
        self.assertEqual(instrument["wordingPolicy"], "immutable")
        self.assertEqual(instrument["scoring"]["status"], "not_configured")
        self.assertIsNone(instrument["scoring"]["score"])
        self.assertEqual(
            [response["questionId"] for response in instrument["responses"]],
            ["prom_phq9_interest", "prom_phq9_item_9"],
        )

    def test_content_hash_changes_if_wording_options_or_order_changes(self):
        original = build_prom_snapshot(QUESTIONS, source_form_id="FRM-02")
        changed = [dict(question) for question in QUESTIONS]
        changed[0]["text"] = "Changed clinical wording"
        modified = build_prom_snapshot(changed, source_form_id="FRM-02")
        self.assertNotEqual(
            original["instruments"][0]["definitionHash"],
            modified["instruments"][0]["definitionHash"],
        )

    def test_duplicate_question_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Duplicate PROM question ID"):
            build_prom_snapshot([QUESTIONS[0], QUESTIONS[0]], source_form_id="FRM-02")

    def test_question_without_stable_id_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "stable question ID"):
            build_prom_snapshot(
                [{"text": "An unidentifiable question"}], source_form_id="FRM-02"
            )

    def test_clinical_instrument_without_explicit_options_fails_closed(self):
        question = dict(QUESTIONS[0])
        question["type"] = "text"
        question["options"] = None
        with self.assertRaisesRegex(ValueError, "explicit response options"):
            build_prom_snapshot([question], source_form_id="FRM-02")

    def test_undeclared_version_is_not_misrepresented_as_approved(self):
        question = {key: value for key, value in QUESTIONS[0].items() if key != "instrument_version"}
        snapshot = build_prom_snapshot([question], source_form_id="FRM-02")
        instrument = snapshot["instruments"][0]
        self.assertIsNone(instrument["declaredVersion"])
        self.assertEqual(instrument["versionStatus"], "content_addressed_only")

    def test_resume_reconstructs_exact_definition(self):
        snapshot = build_prom_snapshot(QUESTIONS, source_form_id="FRM-02")
        restored = questions_from_prom_snapshot(snapshot)
        self.assertEqual(
            [(q["question_id"], q["text"], q["options"]) for q in restored],
            [(q["question_id"], q["text"], q["options"]) for q in QUESTIONS],
        )
        self.assertTrue(all(q["definition_frozen"] for q in restored))

    def test_answer_updates_use_question_id_and_reject_unknown_ids(self):
        snapshot = build_prom_snapshot(QUESTIONS, source_form_id="FRM-02")
        updated = apply_prom_answers(
            snapshot, {"prom_phq9_item_9": "Several days"}
        )
        self.assertEqual(
            updated["instruments"][0]["responses"][1]["value"], "Several days"
        )
        with self.assertRaisesRegex(ValueError, "Unknown PROM question IDs"):
            apply_prom_answers(snapshot, {"prom_phq9_unknown": "value"})

    def test_legacy_text_key_is_adopted_only_on_exact_wording_match(self):
        snapshot = build_prom_snapshot(
            QUESTIONS,
            source_form_id="FRM-02",
            legacy_form_data={
                "PHQ-9 (Depression)": {
                    QUESTIONS[0]["text"]: "Not at all",
                    "Different wording": "Nearly every day",
                }
            },
        )
        values = snapshot["instruments"][0]["responses"]
        self.assertEqual(values[0]["value"], "Not at all")
        self.assertEqual(values[1]["value"], "")


if __name__ == "__main__":
    unittest.main()
