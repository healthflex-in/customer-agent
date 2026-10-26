import unittest

from src.forms.loader import load_form
from src.graph.pure_functions.form_schema import resolve_form_target
from src.graph.pure_functions.lifestyle import (
    clear_lifestyle_component,
    merge_lifestyle_answer,
    missing_lifestyle_components,
    update_lifestyle_component,
)


class LifestyleRetirementTests(unittest.TestCase):
    def test_unrelated_turn_does_not_crash_or_change_lifestyle(self):
        existing = "Historical free text; Custom: preserve me"
        self.assertEqual(
            merge_lifestyle_answer(existing, "Rest helps", "What makes it feel better?"),
            existing,
        )

    def test_work_update_preserves_every_unrelated_semicolon_part(self):
        existing = (
            "Freeform before; Work: old role; Historical status: unchanged; "
            "Activity/exercise: yoga; Freeform after"
        )
        updated = merge_lifestyle_answer(
            existing,
            "architect",
            "What is your work or usual occupation?",
        )
        self.assertEqual(
            updated,
            "Freeform before; Historical status: unchanged; Activity/exercise: yoga; "
            "Freeform after; Work: architect",
        )

    def test_activity_update_preserves_historical_and_arbitrary_text(self):
        existing = (
            "Work: teacher; Smoking: historical patient text; Alcohol: historical text; "
            "Other detail: keep this"
        )
        updated = update_lifestyle_component(existing, "exercise", "daily walking")
        self.assertEqual(
            updated,
            "Work: teacher; Smoking: historical patient text; Alcohol: historical text; "
            "Other detail: keep this; Activity/exercise: daily walking",
        )
        self.assertEqual(missing_lifestyle_components(updated), [])

    def test_clearing_supported_component_preserves_unrelated_text(self):
        existing = "Work: nurse; Legacy: exact value; Activity/exercise: cycling"
        self.assertEqual(
            clear_lifestyle_component(existing, "work"),
            "Legacy: exact value; Activity/exercise: cycling",
        )

    def test_retired_habit_names_are_not_correction_targets(self):
        form = load_form("FRM-01").empty_form()
        self.assertIsNone(resolve_form_target(form, "Current Lifestyle", "Alcohol"))
        self.assertIsNone(resolve_form_target(form, "Current Lifestyle", "Smoking"))
        existing = "Work: nurse; Activity/exercise: cycling; Historical: untouched"
        self.assertIsNone(update_lifestyle_component(existing, "alcohol", "changed"))
        self.assertIsNone(clear_lifestyle_component(existing, "smoking"))


if __name__ == "__main__":
    unittest.main()
