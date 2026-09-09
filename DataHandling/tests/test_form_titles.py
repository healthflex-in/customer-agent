from __future__ import annotations

import unittest

from app.forms.titles import build_form_title


class FormTitleTests(unittest.TestCase):
    def test_builds_title_from_primary_complaint(self) -> None:
        form = {
            "Present Complaint": {
                "Primary Complaint": "knee pain since yesterday",
            }
        }

        self.assertEqual(
            build_form_title(form),
            "Medical Interview: knee pain since yesterday",
        )

    def test_normalizes_whitespace_for_display(self) -> None:
        form = {
            "Present Complaint": {
                "Primary Complaint": "  lower   back\npain  ",
            }
        }

        self.assertEqual(build_form_title(form), "Medical Interview: lower back pain")

    def test_preserves_existing_thirty_character_complaint_limit(self) -> None:
        complaint = "a" * 40
        form = {"Present Complaint": {"Primary Complaint": complaint}}

        title = build_form_title(form)

        self.assertEqual(title, f"Medical Interview: {'a' * 30}")
        self.assertLessEqual(len(title), 50)

    def test_returns_generic_title_when_complaint_is_missing(self) -> None:
        self.assertEqual(build_form_title({}), "Medical Interview Form")

    def test_supports_persistence_empty_form_marker(self) -> None:
        self.assertEqual(build_form_title({}, empty_title="New Form"), "New Form")


if __name__ == "__main__":
    unittest.main()
