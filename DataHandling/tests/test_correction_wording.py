"""A correction that changes the same field for two complaints says which is which."""

import unittest

from src.graph.nodes.correction import _name_changes


FORM = {
    "Present Complaint": {"Primary Complaint": "back pain"},
    "Pain Assessment": {"Primary Location of Pain": "lower back", "Severity (1-10)": "7"},
    "Additional Complaint 1": {"Primary Complaint": "left knee pain", "Severity (1-10)": "5"},
}


class NameChangesTests(unittest.TestCase):
    def test_a_repeated_field_names_its_complaint(self):
        applied = [("Severity (1-10)", "Pain Assessment"), ("Severity (1-10)", "Additional Complaint 1")]

        self.assertEqual(
            _name_changes(applied, FORM),
            ["Severity (1-10) for lower back", "Severity (1-10) for left knee pain"],
        )

    def test_a_single_change_is_unchanged(self):
        self.assertEqual(_name_changes([("Severity (1-10)", "Pain Assessment")], FORM), ["Severity (1-10)"])

    def test_different_fields_are_unchanged(self):
        applied = [("Severity (1-10)", "Pain Assessment"), ("Duration of the Issue", "Present Complaint")]

        self.assertEqual(_name_changes(applied, FORM), ["Severity (1-10)", "Duration of the Issue"])


if __name__ == "__main__":
    unittest.main()
