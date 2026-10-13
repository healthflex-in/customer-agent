from __future__ import annotations

import unittest

from app.ws.idempotency import RecentRequestWindow, RequestDecision


class RecentRequestWindowTests(unittest.TestCase):
    def test_accepts_legacy_requests_without_an_id(self) -> None:
        window = RecentRequestWindow()
        self.assertIs(window.register("patient", None, "question-1"), RequestDecision.ACCEPT)

    def test_rejects_an_exact_request_retry(self) -> None:
        window = RecentRequestWindow()
        first = window.register("patient", "request-1", "question-1")
        retry = window.register("patient", "request-1", "question-1")
        self.assertIs(first, RequestDecision.ACCEPT)
        self.assertIs(retry, RequestDecision.DUPLICATE)

    def test_accepts_identical_answers_using_different_request_ids(self) -> None:
        window = RecentRequestWindow()
        first = window.register("patient", "request-1", "question-1")
        second = window.register("patient", "request-2", "question-2")
        self.assertIs(first, RequestDecision.ACCEPT)
        self.assertIs(second, RequestDecision.ACCEPT)

    def test_scopes_request_ids_by_user(self) -> None:
        window = RecentRequestWindow()
        self.assertIs(window.register("patient-a", "request-1"), RequestDecision.ACCEPT)
        self.assertIs(window.register("patient-b", "request-1"), RequestDecision.ACCEPT)

    def test_detects_request_id_reuse_for_a_different_question(self) -> None:
        window = RecentRequestWindow()
        window.register("patient", "request-1", "question-1")
        self.assertIs(
            window.register("patient", "request-1", "question-2"),
            RequestDecision.CONFLICT,
        )

    def test_evicts_oldest_request_at_capacity(self) -> None:
        window = RecentRequestWindow(capacity=2)
        window.register("patient", "request-1")
        window.register("patient", "request-2")
        window.register("patient", "request-3")
        self.assertIs(window.register("patient", "request-1"), RequestDecision.ACCEPT)

    def test_rejects_malformed_request_ids(self) -> None:
        window = RecentRequestWindow()
        with self.assertRaises(ValueError):
            window.register("patient", "contains spaces")

    def test_rejects_oversized_question_ids(self) -> None:
        window = RecentRequestWindow()
        with self.assertRaises(ValueError):
            window.register("patient", "request-1", "q" * 257)


if __name__ == "__main__":
    unittest.main()
