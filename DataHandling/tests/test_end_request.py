"""Recognising a patient who wants to end the interview, typos included, and no one else."""

import unittest

from src.graph.pure_functions.intent_detection import is_end_request


class EndRequestTests(unittest.TestCase):
    def test_recognises_asking_to_end(self):
        for text in (
            "end my interview",
            "i want to end interview",
            "I want to end the interview",
            "stop the interview",
            "I want to finish the form",
            "no more questions",
            "stop asking",
            "Goodbye",
            "bye",
            "ok bye",
            "I'm done",
            "I'm done with my responses",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_end_request(text))

    def test_recognises_common_typos_of_interview(self):
        for text in ("i want to end interviw", "end interveiw", "end my intervew", "stop the intervie"):
            with self.subTest(text=text):
                self.assertTrue(is_end_request(text))

    def test_ordinary_answers_are_not_end_requests(self):
        for text in (
            "it ends when I rest",
            "I'm done with running because of the pain",
            "my interview went badly",
            "I stopped running last week",
            "that's all I know",
            "end",
            "nothing",
            "",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_end_request(text))


if __name__ == "__main__":
    unittest.main()
