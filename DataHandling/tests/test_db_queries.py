from __future__ import annotations

import unittest

from app.db.queries import (
    build_user_form_queries,
    collection_namespace,
    empty_tagged_questions_result,
)


class FakeObjectId:
    def __init__(self, value: object) -> None:
        value_as_string = str(value)
        if len(value_as_string) != 24:
            raise ValueError("invalid ObjectId")
        self.value = value_as_string

    def __str__(self) -> str:
        return self.value


class BooleanUnsafeCollection:
    name = "customers"

    class Database:
        name = "stance"

    database = Database()

    def __bool__(self) -> bool:
        raise NotImplementedError("PyMongo Collection has no truth value")


class BuildUserFormQueriesTests(unittest.TestCase):
    def test_scopes_object_and_string_user_variants_to_the_form(self) -> None:
        user_id = "67aabbccddeeff0011223344"

        queries = build_user_form_queries(user_id, "FRM-02", FakeObjectId)

        self.assertEqual(len(queries), 2)
        self.assertTrue(all(query["formId"] == "FRM-02" for query in queries))
        self.assertTrue(all(set(query) == {"userId", "formId"} for query in queries))
        self.assertIsInstance(queries[0]["userId"], FakeObjectId)
        self.assertEqual(queries[1]["userId"], user_id)

    def test_never_falls_back_to_a_user_only_query(self) -> None:
        queries = build_user_form_queries(
            "legacy-user", "FRM-03", FakeObjectId
        )

        self.assertEqual(queries, [{"userId": "legacy-user", "formId": "FRM-03"}])

    def test_requires_user_and_form_identifiers(self) -> None:
        with self.assertRaises(ValueError):
            build_user_form_queries("", "FRM-01", FakeObjectId)
        with self.assertRaises(ValueError):
            build_user_form_queries("patient", "", FakeObjectId)

    def test_optional_attempt_id_scopes_every_identity_variant(self) -> None:
        queries = build_user_form_queries(
            "67aabbccddeeff0011223344",
            "FRM-01",
            FakeObjectId,
            attempt_id="ATT-123",
        )

        self.assertEqual(len(queries), 2)
        self.assertTrue(all(query["attemptId"] == "ATT-123" for query in queries))

    def test_empty_explicit_attempt_id_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_user_form_queries(
                "patient", "FRM-01", FakeObjectId, attempt_id=" "
            )


class CollectionNamespaceTests(unittest.TestCase):
    def test_does_not_evaluate_collection_truthiness(self) -> None:
        collection = BooleanUnsafeCollection()

        result = collection_namespace(collection, "users")

        self.assertEqual(result, ("stance", "customers"))

    def test_returns_defaults_for_missing_collection(self) -> None:
        self.assertEqual(collection_namespace(None, "users"), ("unknown", "users"))


class TaggedQuestionResultTests(unittest.TestCase):
    def test_empty_result_always_has_the_three_value_contract(self) -> None:
        questions, template, source_document_id = empty_tagged_questions_result()

        self.assertEqual(questions, [])
        self.assertEqual(template, {})
        self.assertIsNone(source_document_id)


if __name__ == "__main__":
    unittest.main()
