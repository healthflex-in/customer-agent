import copy
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

try:
    from bson import ObjectId
except ModuleNotFoundError:
    class ObjectId(str):
        def __new__(cls, value=None):
            return super().__new__(cls, value or "507f1f77bcf86cd799439099")

    bson_module = types.ModuleType("bson")
    bson_module.ObjectId = ObjectId
    sys.modules["bson"] = bson_module

try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = lambda *_args, **_kwargs: None
    sys.modules["dotenv"] = dotenv_module

from app.forms.repository import fetch_user_forms, save_customer_info
from app.forms.sanitization import (
    preserve_historical_referral_section,
    strip_deprecated_referral_section,
)


class _Cursor(list):
    def sort(self, *_args):
        return self


class ReferralPersistenceTests(unittest.TestCase):
    def test_new_form_sanitizer_strips_referral_without_mutating_caller(self):
        form_data = {
            "Present Complaint": {"Primary Complaint": "Knee pain"},
            "History & Diagnostics": {"Source": "Patient statement"},
            "Referral": {"Source": "Google"},
        }
        persisted = strip_deprecated_referral_section(form_data)
        self.assertNotIn("Referral", persisted)
        self.assertEqual(persisted["History & Diagnostics"]["Source"], "Patient statement")
        self.assertEqual(form_data["Referral"], {"Source": "Google"})

    def test_preservation_helper_deep_copies_exact_historical_value(self):
        active = {"Present Complaint": {"Primary Complaint": "Updated"}}
        historical = {
            "Referral": {
                "Source": "Friend",
                "metadata": {"campaign": ["old", {"exact": True}]},
            }
        }
        result = preserve_historical_referral_section(active, historical)
        self.assertEqual(result["Referral"], historical["Referral"])
        self.assertIsNot(result["Referral"], historical["Referral"])
        self.assertIsNot(result["Referral"]["metadata"], historical["Referral"]["metadata"])
        self.assertNotIn("Referral", active)

    @patch("app.forms.repository.resolve_assessment_appointment_id", return_value=None)
    @patch("app.forms.repository.mongo.customer_info_collection")
    def test_existing_save_reuses_lookup_and_preserves_referral_exactly(
        self, collection, _resolve_appointment
    ):
        user_id = "507f1f77bcf86cd799439011"
        historical_referral = {
            "Source": "Friend",
            "nested": {"values": [1, {"raw": "unchanged"}]},
        }
        existing = {
            "_id": ObjectId(),
            "status": "in_progress",
            "form_data": {
                "Present Complaint": {"Primary Complaint": "Old"},
                "Referral": historical_referral,
            },
        }
        collection.find_one.return_value = existing
        incoming = {
            "Present Complaint": {"Primary Complaint": "Updated"},
            "Referral": {"Source": "Injected replacement"},
        }
        incoming_before = copy.deepcopy(incoming)
        existing_before = copy.deepcopy(existing)

        result = save_customer_info(user_id, incoming, "Present Complaint", "FRM-01")

        self.assertEqual(result, "FRM-01")
        self.assertEqual(collection.find_one.call_count, 1)
        collection.insert_one.assert_not_called()
        persisted = collection.update_one.call_args.args[1]["$set"]["form_data"]
        self.assertEqual(persisted["Referral"], historical_referral)
        self.assertIsNot(persisted["Referral"], historical_referral)
        self.assertEqual(persisted["Present Complaint"]["Primary Complaint"], "Updated")
        self.assertEqual(incoming, incoming_before)
        self.assertEqual(existing, existing_before)

    @patch("app.forms.repository.resolve_assessment_appointment_id", return_value=None)
    @patch("app.forms.repository.mongo.customer_info_collection")
    def test_new_save_omits_injected_referral(self, collection, _resolve_appointment):
        collection.find_one.return_value = None
        incoming = {
            "Present Complaint": {"Primary Complaint": "Knee pain"},
            "Referral": {"Source": "Injected"},
        }
        save_customer_info("507f1f77bcf86cd799439011", incoming, "Present Complaint", "FRM-01")
        persisted = collection.insert_one.call_args.args[0]["form_data"]
        self.assertNotIn("Referral", persisted)
        self.assertIn("Referral", incoming)

    @patch("app.forms.repository.mongo.customer_info_collection")
    def test_form_listing_returns_progress_instead_of_swallowing_import_error(self, collection):
        collection.find.return_value = _Cursor([
            {
                "formId": "FRM-01",
                "title": "Knee intake",
                "form_data": {"Present Complaint": {"Primary Complaint": "Knee pain"}},
            }
        ])
        forms = fetch_user_forms("507f1f77bcf86cd799439011")
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0]["formId"], "FRM-01")
        self.assertEqual(forms[0]["progress"], 100)


if __name__ == "__main__":
    unittest.main()
