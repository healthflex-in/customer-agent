"""Deciding whether two complaint labels, or a statement, mean one problem."""

import unittest

from src.graph.pure_functions.additional_complaint import (
    is_same_complaint_statement,
    same_complaint,
    same_complaint_section,
)


class SameComplaintTests(unittest.TestCase):
    def test_a_qualifier_does_not_make_a_new_complaint(self):
        for first, second in (
            ("back pain", "lower back pain"),
            ("Lower back pain", "Back pain"),
            ("knee pain", "right knee pain"),
            ("Back pain", "back pain"),
        ):
            with self.subTest(first=first, second=second):
                self.assertTrue(same_complaint(first, second))

    def test_different_body_areas_or_sides_are_different_complaints(self):
        for first, second in (
            ("knee pain", "back pain"),
            ("left knee pain", "right knee pain"),
            ("Lumbar facet joint pain", "Knee pain"),
            ("neck pain", "back pain"),
        ):
            with self.subTest(first=first, second=second):
                self.assertFalse(same_complaint(first, second))

    def test_labels_with_no_distinguishing_words_compare_as_text(self):
        self.assertTrue(same_complaint("Pain", "pain"))
        self.assertFalse(same_complaint("Pain", "Ache"))


class SameIssueStatementTests(unittest.TestCase):
    def test_recognises_the_patient_saying_it_is_one_problem(self):
        for text in (
            "Lower back pain is not a different issue. It is the same issue.",
            "Lower back pain is not another concern it is the same concern",
            "it's the same",
            "It\u2019s the same",
            "that is the same problem",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_same_complaint_statement(text))

    def test_ordinary_answers_are_not_read_as_same_issue(self):
        for text in (
            "the pain is the same as before",
            "same pain as yesterday",
            "it hurts more",
            "I have knee pain too",
            "nothing gives relief",
            "",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_same_complaint_statement(text))


class SameComplaintSectionTests(unittest.TestCase):
    def _form(self, *extras):
        form = {"Present Complaint": {"Primary Complaint": "Back pain"}}
        for index, label in enumerate(extras, 1):
            form[f"Additional Complaint {index}"] = {"Primary Complaint": label}
        return form

    def test_returns_the_extra_complaint_that_restates_the_main_one(self):
        form = self._form("Lower back pain")

        self.assertEqual(same_complaint_section("it is the same issue", form), "Additional Complaint 1")

    def test_never_returns_a_genuine_second_complaint(self):
        self.assertIsNone(same_complaint_section("it is the same issue", self._form("Knee pain")))

    def test_does_nothing_when_the_patient_did_not_say_it_is_the_same(self):
        self.assertIsNone(same_complaint_section("it hurts more", self._form("Lower back pain")))

    def test_is_ambiguous_when_two_extras_restate_the_main_one(self):
        form = self._form("Lower back pain", "Back pain")

        self.assertIsNone(same_complaint_section("it is the same issue", form))


if __name__ == "__main__":
    unittest.main()
