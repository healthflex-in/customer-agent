"""Background loop that claims and publishes report-summary jobs."""

import asyncio
import os
import uuid
from datetime import datetime, timezone

from app.config import (
    MAX_ATTACHMENT_TOTAL_MB,
    REPORT_JOB_LEASE_SECONDS,
    REPORT_JOB_MAX_ATTEMPTS,
    REPORT_JOB_POLL_SECONDS,
    REPORT_JOB_RETRY_BASE_SECONDS,
    REPORT_JOB_RETRY_MAX_SECONDS,
)
from app.db.mongo import mongo
from app.jobs.reports import (
    FAILED as REPORT_JOB_FAILED,
    claim_report_job,
    mark_report_job_completed,
    mark_report_job_failed,
    renew_report_job_lease,
    render_report_summary,
)
from app.observability.logging import log as _log
from app.observability.privacy import error_type
from app.runtime.blocking import run_blocking
from upload.s3_client import download_bytes_from_s3


async def _process_report_job(job: dict, worker_id: str) -> None:
    """Process one leased report job and atomically publish only the latest result."""

    from docscanner.service import summarize_multiple_reports

    try:
        max_total_bytes = MAX_ATTACHMENT_TOTAL_MB * 1024 * 1024
        file_data_list = []
        downloaded_bytes = 0
        for item in job.get("objects", []):
            remaining = max_total_bytes - downloaded_bytes
            if remaining <= 0:
                raise ValueError("Report job exceeds the aggregate download limit")
            content = await run_blocking(
                download_bytes_from_s3,
                item["key"],
                remaining,
            )
            downloaded_bytes += len(content)
            file_data_list.append((content, item["filename"]))
        if not file_data_list:
            raise ValueError("Report job contains no objects")

        summary = await run_blocking(summarize_multiple_reports, file_data_list)
        if not summary or summary.get("error"):
            raise RuntimeError("Report summarizer returned no usable result")
        lease_is_current = await run_blocking(
            renew_report_job_lease,
            mongo.report_jobs_collection,
            job["_id"],
            worker_id=worker_id,
            lease_seconds=REPORT_JOB_LEASE_SECONDS,
        )
        if not lease_is_current:
            _log.info("report_job_lease_lost", job_id=job["_id"])
            return
        reports_text = render_report_summary(summary, len(file_data_list))
        now = datetime.now(timezone.utc)
        result = await run_blocking(
            mongo.customer_info_collection.update_one,
            {
                "formId": job["formId"],
                "userId": job["userId"],
                "reportProcessing.jobId": job["_id"],
            },
            {
                "$set": {
                    "form_data.History & Diagnostics.Reports": reports_text,
                    "reportProcessing.status": "completed",
                    "reportProcessing.completedAt": now,
                    "updatedAt": now,
                }
            },
        )
        if result.matched_count == 0:
            _log.info("report_job_result_superseded", job_id=job["_id"])
        await run_blocking(
            mark_report_job_completed,
            mongo.report_jobs_collection,
            job["_id"],
            worker_id=worker_id,
        )
        _log.info("report_job_completed", job_id=job["_id"])
    except Exception as exc:
        status = await run_blocking(
            mark_report_job_failed,
            mongo.report_jobs_collection,
            job,
            worker_id=worker_id,
            error_name=error_type(exc),
            max_attempts=REPORT_JOB_MAX_ATTEMPTS,
            retry_base_seconds=REPORT_JOB_RETRY_BASE_SECONDS,
            retry_max_seconds=REPORT_JOB_RETRY_MAX_SECONDS,
        )
        _log.warning(
            "report_job_attempt_failed",
            job_id=job.get("_id"),
            status=status,
            error_type=error_type(exc),
        )
        if status == REPORT_JOB_FAILED and mongo.customer_info_collection is not None:
            await run_blocking(
                mongo.customer_info_collection.update_one,
                {
                    "formId": job["formId"],
                    "userId": job["userId"],
                    "reportProcessing.jobId": job["_id"],
                },
                {
                    "$set": {
                        "reportProcessing.status": "failed",
                        "reportProcessing.failedAt": datetime.now(timezone.utc),
                    }
                },
            )


async def _report_worker_loop(stop_event: asyncio.Event) -> None:
    """Claim durable jobs one at a time; leases recover work after a crash."""

    worker_id = f"{os.getpid()}-{uuid.uuid4().hex}"
    while stop_event is not None and not stop_event.is_set():
        try:
            job = await run_blocking(
                claim_report_job,
                mongo.report_jobs_collection,
                worker_id=worker_id,
                lease_seconds=REPORT_JOB_LEASE_SECONDS,
                max_attempts=REPORT_JOB_MAX_ATTEMPTS,
            )
            if job:
                await _process_report_job(job, worker_id)
                continue
        except Exception as exc:
            _log.warning("report_worker_poll_failed", error_type=error_type(exc))
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=REPORT_JOB_POLL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass
