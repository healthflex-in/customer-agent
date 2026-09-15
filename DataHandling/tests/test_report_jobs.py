from datetime import datetime, timezone
from pathlib import Path
import unittest

from app.jobs.reports import (
    COMPLETED,
    FAILED,
    PROCESSING,
    RETRY,
    build_report_job,
    claim_report_job,
    mark_report_job_completed,
    mark_report_job_failed,
    render_report_summary,
    renew_report_job_lease,
    retry_delay_seconds,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class RecordingCollection:
    def __init__(self, claimed=None):
        self.claimed = claimed
        self.find_call = None
        self.update_call = None

    def find_one_and_update(self, query, update, **kwargs):
        self.find_call = (query, update, kwargs)
        return self.claimed

    def update_one(self, query, update):
        self.update_call = (query, update)
        return type("UpdateResult", (), {"modified_count": 1})()


class ReportJobTests(unittest.TestCase):
    def test_job_contains_only_s3_references_not_medical_bytes(self):
        job = build_report_job(
            job_id="job-1",
            user_id="patient-1",
            form_id="FRM-01",
            objects=[{"key": "private/key.pdf", "filename": "report.pdf"}],
            now=NOW,
        )
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempts"], 0)
        self.assertNotIn("bytes", repr(job).lower())

    def test_invalid_job_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            build_report_job(job_id="", user_id="u", form_id="f", objects=[])

    def test_claim_includes_retry_and_expired_lease_and_increments_attempt(self):
        collection = RecordingCollection({"_id": "job-1"})
        result = claim_report_job(
            collection,
            worker_id="worker-1",
            lease_seconds=60,
            max_attempts=3,
            now=NOW,
        )
        query, update, kwargs = collection.find_call
        self.assertEqual(result, {"_id": "job-1"})
        self.assertIn("$or", query)
        self.assertEqual(query["attempts"], {"$lt": 3})
        self.assertEqual(update["$inc"], {"attempts": 1})
        self.assertEqual(update["$set"]["status"], PROCESSING)
        self.assertIn("sort", kwargs)

    def test_retry_backoff_is_exponential_and_capped(self):
        self.assertEqual(retry_delay_seconds(1, 30, 100), 30)
        self.assertEqual(retry_delay_seconds(2, 30, 100), 60)
        self.assertEqual(retry_delay_seconds(3, 30, 100), 100)

    def test_failure_retries_before_terminal_attempt(self):
        collection = RecordingCollection()
        status = mark_report_job_failed(
            collection,
            {"_id": "job-1", "attempts": 1},
            worker_id="worker-1",
            error_name="TimeoutError",
            max_attempts=3,
            retry_base_seconds=30,
            retry_max_seconds=300,
            now=NOW,
        )
        self.assertEqual(status, RETRY)
        self.assertEqual(collection.update_call[1]["$set"]["status"], RETRY)
        self.assertIn("nextAttemptAt", collection.update_call[1]["$set"])

    def test_final_failure_has_no_next_attempt(self):
        collection = RecordingCollection()
        status = mark_report_job_failed(
            collection,
            {"_id": "job-1", "attempts": 3},
            worker_id="worker-1",
            error_name="RuntimeError",
            max_attempts=3,
            retry_base_seconds=30,
            retry_max_seconds=300,
            now=NOW,
        )
        self.assertEqual(status, FAILED)
        self.assertIn("nextAttemptAt", collection.update_call[1]["$unset"])

    def test_completion_is_lease_owner_guarded(self):
        collection = RecordingCollection()
        mark_report_job_completed(collection, "job-1", worker_id="worker-1", now=NOW)
        query, update = collection.update_call
        self.assertEqual(query["leaseOwner"], "worker-1")
        self.assertEqual(update["$set"]["status"], COMPLETED)

    def test_lease_renewal_is_owner_guarded(self):
        collection = RecordingCollection()
        self.assertTrue(
            renew_report_job_lease(
                collection,
                "job-1",
                worker_id="worker-1",
                lease_seconds=60,
                now=NOW,
            )
        )
        query, update = collection.update_call
        self.assertEqual(query["leaseOwner"], "worker-1")
        self.assertGreater(update["$set"]["leaseUntil"], NOW)

    def test_summary_rendering_is_deterministic(self):
        text = render_report_summary(
            {
                "findings": [{"title": "Knee", "details": "No fracture"}],
                "measurements": [
                    {"label": "Effusion", "value": "2", "units": "mm"}
                ],
                "impression": "Mild inflammation",
            },
            1,
        )
        self.assertIn("- Knee: No fracture", text)
        self.assertIn("- Effusion: 2 mm", text)
        self.assertIn("Impression: Mild inflammation", text)

    def test_failure_text_does_not_expose_provider_error(self):
        text = render_report_summary({"error": "secret provider detail"}, 2)
        self.assertNotIn("secret", text)
        self.assertEqual(text, "Uploaded 2 document(s). Summary processing failed.")

    def test_only_lifecycle_supervised_worker_uses_create_task(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")
        self.assertEqual(server_source.count("asyncio.create_task("), 1)
        self.assertIn(
            "asyncio.create_task(_report_worker_loop())",
            server_source,
        )
        self.assertNotIn("create_task(run_blocking(", server_source)

    def test_report_publish_is_atomic_and_latest_job_guarded(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")
        self.assertIn('"reportProcessing.jobId": job["_id"]', server_source)
        self.assertIn(
            '"form_data.History & Diagnostics.Reports": reports_text',
            server_source,
        )
        self.assertNotIn('{"$set": {"form_data": _fd', server_source)

    def test_job_marker_is_persisted_before_job_becomes_claimable(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")
        route_start = server_source.index("async def upload_form_attachment")
        marker = server_source.index('"reportProcessing": {', route_start)
        enqueue = server_source.index("report_jobs_collection.insert_one", marker)
        self.assertLess(marker, enqueue)

    def test_s3_worker_download_has_an_explicit_byte_ceiling(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")
        worker_start = server_source.index("async def _process_report_job")
        download = server_source.index("download_bytes_from_s3,", worker_start)
        remaining = server_source.index("remaining,", download)
        summarize = server_source.index(
            "summary = await run_blocking(summarize_multiple_reports",
            download,
        )
        self.assertLess(remaining, summarize)

    def test_backlog_capacity_is_checked_before_s3_upload(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")
        route_start = server_source.index("async def upload_form_attachment")
        capacity = server_source.index(
            "active_report_jobs = await run_blocking(",
            route_start,
        )
        upload = server_source.index("upload_bytes_to_s3,", capacity)
        self.assertLess(capacity, upload)
        self.assertIn("REPORT_JOB_MAX_BACKLOG", server_source[capacity:upload])


if __name__ == "__main__":
    unittest.main()
