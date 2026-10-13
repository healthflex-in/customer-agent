import unittest

from src.graph.pure_functions.intent_detection import should_check_for_correction


class InterviewCorrectionRoutingRegressionTests(unittest.TestCase):
    def test_client_screenshot_answers_are_not_corrections(self):
        normal_answers = (
            "Actually, I have cough and cold a lot.",
            (
                "I would rate it around six. When I drink cold water the cold gets worse, "
                "but when I drink hot water I get relief. I didn't, yes, I did see a "
                "physiotherapist or a doctor. I have a headache as another condition, "
                "and I have surgery for migraine. Actually, I work in a company and I "
                "drink and smoke as well."
            ),
            (
                "Sorry, but I have given all the information. Why are you asking the "
                "same question again? I am not asking for a change."
            ),
        )

        for answer in normal_answers:
            with self.subTest(answer=answer):
                self.assertFalse(should_check_for_correction(answer))

    def test_only_explicit_revisions_are_detected_by_legacy_helper(self):
        explicit_corrections = (
            "I previously said right knee, but I meant left knee.",
            "Please change my pain score from 6 to 4.",
            "That is wrong; the correct duration should be two weeks.",
            "Sorry, I misspoke. It is my left knee.",
        )

        for correction in explicit_corrections:
            with self.subTest(correction=correction):
                self.assertTrue(should_check_for_correction(correction))

    def test_discourse_words_alone_are_not_corrections(self):
        for answer in ("actually", "sorry", "instead", "rather", "to clarify"):
            with self.subTest(answer=answer):
                self.assertFalse(should_check_for_correction(answer))


if __name__ == "__main__":
    unittest.main()
