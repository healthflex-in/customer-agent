from datetime import datetime, timedelta, timezone
import unittest

from app.forms.lifecycle import (
    COMPLETED,
    DRAFT,
    IN_PROGRESS,
    TTL_INDEX_NAME,
    build_lifecycle_update_filter,
    ensure_form_lifecycle_ttl_index,
    has_meaningful_form_data,
    resolve_form_lifecycle,
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
        self.indexes.pop(name, None)

    def create_index(self, key, **kwargs):
        self.created.append((key, kwargs))


class FormLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)

    def test_nested_empty_prom_skeleton_is_a_draft_with_expiration(self):
        lifecycle = resolve_form_lifecycle(
            {"PHQ-9": {"Question 1": "", "Question 2": None}},
            now=self.now,
        )

        self.assertEqual(lifecycle.status, DRAFT)
        self.assertEqual(lifecycle.expires_at, self.now + timedelta(days=7))
        self.assertIsNone(lifecycle.completed_at)

    def test_any_meaningful_answer_removes_expiration(self):
        lifecycle = resolve_form_lifecycle(
            {"PHQ-9": {"Question 1": "Several days", "Question 2": ""}},
            now=self.now,
        )

        self.assertEqual(lifecycle.status, IN_PROGRESS)
        self.assertIsNone(lifecycle.expires_at)

    def test_explicit_completion_is_timestamped_without_expiration(self):
        lifecycle = resolve_form_lifecycle(
            {},
            requested_status=COMPLETED,
            now=self.now,
        )

        self.assertEqual(lifecycle.status, COMPLETED)
        self.assertEqual(lifecycle.completed_at, self.now)
        self.assertIsNone(lifecycle.expires_at)

    def test_completed_status_cannot_be_downgraded_by_a_later_save(self):
        original_completion = self.now - timedelta(hours=2)
        lifecycle = resolve_form_lifecycle(
            {},
            requested_status=DRAFT,
            existing_status=COMPLETED,
            existing_completed_at=original_completion,
            now=self.now,
        )

        self.assertEqual(lifecycle.status, COMPLETED)
        self.assertEqual(lifecycle.completed_at, original_completion)

    def test_ordinary_update_is_atomically_blocked_after_completion(self):
        self.assertEqual(
            build_lifecycle_update_filter("document-1", requested_status=None),
            {"_id": "document-1", "status": {"$ne": COMPLETED}},
        )

    def test_explicit_completion_can_update_the_terminal_snapshot(self):
        self.assertEqual(
            build_lifecycle_update_filter(
                "document-1",
                requested_status=COMPLETED,
            ),
            {"_id": "document-1"},
        )

    def test_rejects_invalid_status_and_ttl(self):
        with self.assertRaises(ValueError):
            resolve_form_lifecycle({}, requested_status="deleted")
        with self.assertRaises(ValueError):
            resolve_form_lifecycle({}, draft_ttl_days=0)

    def test_meaningful_data_handles_nested_collections_and_whitespace(self):
        self.assertFalse(has_meaningful_form_data({"a": [" ", None, {}]}))
        self.assertTrue(has_meaningful_form_data({"a": ["", 0]}))

    def test_replaces_legacy_title_based_ttl_index(self):
        collection = FakeCollection({
            TTL_INDEX_NAME: {
                "key": [("createdAt", 1)],
                "expireAfterSeconds": 604800,
                "partialFilterExpression": {"title": "New Form"},
            }
        })

        ensure_form_lifecycle_ttl_index(collection)

        self.assertEqual(collection.dropped, [TTL_INDEX_NAME])
        key, options = collection.created[-1]
        self.assertEqual(key, [("expiresAt", 1)])
        self.assertEqual(options["expireAfterSeconds"], 0)
        self.assertEqual(options["partialFilterExpression"], {"status": DRAFT})

    def test_keeps_matching_lifecycle_index(self):
        collection = FakeCollection({
            TTL_INDEX_NAME: {
                "key": [("expiresAt", 1)],
                "expireAfterSeconds": 0,
                "partialFilterExpression": {"status": DRAFT},
            }
        })

        ensure_form_lifecycle_ttl_index(collection)

        self.assertEqual(collection.dropped, [])
        self.assertEqual(len(collection.created), 1)


if __name__ == "__main__":
    unittest.main()
