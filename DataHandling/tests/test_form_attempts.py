from __future__ import annotations

import unittest
import uuid

from app.forms.attempts import (
    ATTEMPT_INDEX_KEYS,
    ATTEMPT_INDEX_NAME,
    add_attempt_scope,
    add_attempt_write_scope,
    ensure_form_attempt_index,
    new_form_attempt_id,
    resolve_form_attempt_id,
)


class FakeCollection:
    def __init__(self, indexes=None):
        self.indexes = indexes or {}
        self.dropped = []
        self.created = []

    def index_information(self):
        return self.indexes

    def drop_index(self, name):
        self.dropped.append(name)

    def create_index(self, keys, **options):
        self.created.append((keys, options))


class FormAttemptTests(unittest.TestCase):
    def test_generated_attempt_ids_are_opaque_and_unique(self):
        first = new_form_attempt_id()
        second = new_form_attempt_id()

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("ATT-"))
        uuid.UUID(first.removeprefix("ATT-"))

    def test_resume_reuses_exact_attempt_identity(self):
        self.assertEqual(
            resolve_form_attempt_id({"attemptId": "ATT-existing"}),
            "ATT-existing",
        )

    def test_legacy_resume_does_not_silently_create_a_copy(self):
        self.assertIsNone(resolve_form_attempt_id({"formId": "FRM-01"}))

    def test_explicit_new_intake_never_reuses_existing_attempt(self):
        self.assertEqual(
            resolve_form_attempt_id(
                {"attemptId": "ATT-existing"},
                force_new=True,
                id_factory=lambda: "ATT-new",
            ),
            "ATT-new",
        )

    def test_attempt_scope_is_exact_and_does_not_mutate_input(self):
        original = {"userId": "patient", "formId": "FRM-01"}

        scoped = add_attempt_scope(original, " ATT-123 ")

        self.assertEqual(original, {"userId": "patient", "formId": "FRM-01"})
        self.assertEqual(scoped["attemptId"], "ATT-123")

    def test_legacy_query_remains_supported_when_attempt_is_absent(self):
        query = {"userId": "patient", "formId": "FRM-01"}
        self.assertEqual(add_attempt_scope(query, None), query)

    def test_legacy_write_cannot_match_a_newer_attempt(self):
        query = {"userId": "patient", "formId": "FRM-01"}

        self.assertEqual(
            add_attempt_write_scope(query, None),
            {
                "userId": "patient",
                "formId": "FRM-01",
                "attemptId": {"$exists": False},
            },
        )

    def test_new_write_is_scoped_to_the_exact_attempt(self):
        query = {"userId": "patient", "formId": "FRM-01"}

        self.assertEqual(
            add_attempt_write_scope(query, "ATT-123")["attemptId"],
            "ATT-123",
        )

    def test_empty_explicit_attempt_is_rejected(self):
        with self.assertRaises(ValueError):
            add_attempt_scope({"userId": "patient", "formId": "FRM-01"}, " ")

    def test_index_migration_drops_only_the_legacy_unique_constraint(self):
        collection = FakeCollection(
            {
                "_id_": {"key": [("_id", 1)], "unique": True},
                "unique_user_form": {
                    "key": [("userId", 1), ("formId", 1)],
                    "unique": True,
                },
                "user_lookup": {"key": [("userId", 1)]},
            }
        )

        ensure_form_attempt_index(collection)

        self.assertEqual(collection.dropped, ["unique_user_form"])
        self.assertEqual(collection.created[-1][0], ATTEMPT_INDEX_KEYS)
        self.assertEqual(
            collection.created[-1][1],
            {"unique": True, "name": ATTEMPT_INDEX_NAME},
        )

    def test_unexpected_attempt_index_fails_closed(self):
        collection = FakeCollection(
            {
                ATTEMPT_INDEX_NAME: {
                    "key": [("attemptId", 1)],
                    "unique": True,
                }
            }
        )

        with self.assertRaises(RuntimeError):
            ensure_form_attempt_index(collection)


if __name__ == "__main__":
    unittest.main()
