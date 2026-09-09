from __future__ import annotations

import unittest
from types import ModuleType
from unittest.mock import patch

from app.db.ownership import build_owned_form_filter


class FakeObjectId(str):
    pass


class BuildOwnedFormFilterTests(unittest.TestCase):
    def test_builds_exact_filter_and_restores_serialized_object_id(self) -> None:
        document_id = "67aabbccddeeff0011223344"
        user_id = "67aabbccddeeff0011223355"
        form = {
            "_id": document_id,
            "userId": user_id,
            "formId": "FRM-01",
        }

        fake_bson = ModuleType("bson")
        fake_bson.ObjectId = FakeObjectId
        with patch.dict("sys.modules", {"bson": fake_bson}):
            result = build_owned_form_filter(form, user_id, "FRM-01")

        self.assertEqual(
            result,
            {
                "_id": FakeObjectId(document_id),
                "userId": user_id,
                "formId": "FRM-01",
            },
        )

    def test_preserves_legacy_string_identifiers(self) -> None:
        form = {
            "_id": "legacy-document-id",
            "userId": "legacy-user-id",
            "formId": "FRM-01",
        }

        result = build_owned_form_filter(form, "legacy-user-id", "FRM-01")

        self.assertEqual(result["_id"], "legacy-document-id")
        self.assertEqual(result["userId"], "legacy-user-id")

    def test_rejects_a_document_owned_by_another_user(self) -> None:
        form = {
            "_id": "legacy-document-id",
            "userId": "patient-a",
            "formId": "FRM-01",
        }

        with self.assertRaises(PermissionError):
            build_owned_form_filter(form, "patient-b", "FRM-01")

    def test_rejects_a_different_form(self) -> None:
        form = {
            "_id": "legacy-document-id",
            "userId": "patient-a",
            "formId": "FRM-02",
        }

        with self.assertRaises(ValueError):
            build_owned_form_filter(form, "patient-a", "FRM-01")

    def test_rejects_a_document_without_an_id(self) -> None:
        form = {"userId": "patient-a", "formId": "FRM-01"}

        with self.assertRaises(ValueError):
            build_owned_form_filter(form, "patient-a", "FRM-01")


if __name__ == "__main__":
    unittest.main()
