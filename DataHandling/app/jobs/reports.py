"""Mongo-backed report summarization jobs with leases and bounded retries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

QUEUED = "queued"
PROCESSING = "processing"
RETRY = "retry"
COMPLETED = "completed"
FAILED = "failed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_report_job(
    *,
    job_id: str,
    user_id: Any,
    form_id: str,
    objects: Iterable[dict],
    now: datetime | None = None,
) -> dict:
    object_list = [
        {"key": str(item["key"]), "filename": str(item["filename"])}
        for item in objects
    ]
    if not job_id or not form_id or user_id is None or not object_list:
        raise ValueError("Report job requires an id, owner, form, and objects")
    created_at = now or utc_now()
    return {
        "_id": job_id,
        "type": "report_summary",
        "userId": user_id,
        "formId": form_id,
        "objects": object_list,
        "status": QUEUED,
        "attempts": 0,
        "nextAttemptAt": created_at,
        "createdAt": created_at,
        "updatedAt": created_at,
    }


def ensure_report_job_indexes(collection) -> None:
    collection.create_index(
        [("status", 1), ("nextAttemptAt", 1), ("createdAt", 1)],
        name="report_job_claim",
    )


def claim_report_job(
    collection,
    *,
    worker_id: str,
    lease_seconds: int,
    max_attempts: int,
    now: datetime | None = None,
) -> dict | None:
    if not worker_id or min(lease_seconds, max_attempts) <= 0:
        raise ValueError("Worker lease and retry settings must be positive")
    claimed_at = now or utc_now()
    return collection.find_one_and_update(
        {
            "type": "report_summary",
            "attempts": {"$lt": max_attempts},
            "$or": [
                {
                    "status": {"$in": [QUEUED, RETRY]},
                    "nextAttemptAt": {"$lte": claimed_at},
                },
                {
                    "status": PROCESSING,
                    "leaseUntil": {"$lte": claimed_at},
                },
            ],
        },
        {
            "$set": {
                "status": PROCESSING,
                "leaseOwner": worker_id,
                "leaseUntil": claimed_at + timedelta(seconds=lease_seconds),
                "updatedAt": claimed_at,
            },
            "$inc": {"attempts": 1},
            "$unset": {"lastError": "", "nextAttemptAt": ""},
        },
        sort=[("createdAt", 1)],
        # PyMongo's ReturnDocument.AFTER is the boolean value True. Keeping the
        # policy module driver-agnostic makes it independently testable.
        return_document=True,
    )


def retry_delay_seconds(attempts: int, base_seconds: int, max_seconds: int) -> int:
    if min(attempts, base_seconds, max_seconds) <= 0:
        raise ValueError("Retry settings must be positive")
    return min(max_seconds, base_seconds * (2 ** (attempts - 1)))


def mark_report_job_failed(
    collection,
    job: dict,
    *,
    worker_id: str,
    error_name: str,
    max_attempts: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
    now: datetime | None = None,
) -> str:
    attempts = int(job.get("attempts", 0))
    failed_at = now or utc_now()
    terminal = attempts >= max_attempts
    status = FAILED if terminal else RETRY
    fields = {
        "status": status,
        "lastError": error_name[:120],
        "updatedAt": failed_at,
    }
    if not terminal:
        fields["nextAttemptAt"] = failed_at + timedelta(
            seconds=retry_delay_seconds(
                attempts,
                retry_base_seconds,
                retry_max_seconds,
            )
        )
    update: dict[str, dict] = {
        "$set": fields,
        "$unset": {"leaseOwner": "", "leaseUntil": ""},
    }
    if terminal:
        update["$unset"]["nextAttemptAt"] = ""
    collection.update_one(
        {"_id": job["_id"], "status": PROCESSING, "leaseOwner": worker_id},
        update,
    )
    return status


def mark_report_job_completed(
    collection,
    job_id: str,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> None:
    completed_at = now or utc_now()
    collection.update_one(
        {"_id": job_id, "status": PROCESSING, "leaseOwner": worker_id},
        {
            "$set": {
                "status": COMPLETED,
                "completedAt": completed_at,
                "updatedAt": completed_at,
            },
            "$unset": {"leaseOwner": "", "leaseUntil": "", "lastError": ""},
        },
    )


def renew_report_job_lease(
    collection,
    job_id: str,
    *,
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> bool:
    if lease_seconds <= 0:
        raise ValueError("Worker lease must be positive")
    renewed_at = now or utc_now()
    result = collection.update_one(
        {"_id": job_id, "status": PROCESSING, "leaseOwner": worker_id},
        {
            "$set": {
                "leaseUntil": renewed_at + timedelta(seconds=lease_seconds),
                "updatedAt": renewed_at,
            }
        },
    )
    return result.modified_count == 1


def render_report_summary(summary: dict | None, document_count: int) -> str:
    if not summary or summary.get("error"):
        return f"Uploaded {document_count} document(s). Summary processing failed."

    lines: list[str] = []
    findings = summary.get("findings") or []
    if findings:
        lines.append("Findings:")
        for finding in findings:
            title = str(finding.get("title", "")).strip()
            details = str(finding.get("details", "")).strip()
            lines.append(f"- {title}: {details}" if title else f"- {details}")

    measurements = summary.get("measurements") or []
    if measurements:
        if lines:
            lines.append("")
        lines.append("Measurements:")
        for measurement in measurements:
            label = str(measurement.get("label", "")).strip()
            value = str(measurement.get("value", "")).strip()
            units = str(measurement.get("units", "")).strip()
            location = str(measurement.get("anatomical_location", "")).strip()
            if label and value:
                rendered = f"- {label}: {value}{' ' + units if units else ''}"
                if location:
                    rendered += f" ({location})"
                lines.append(rendered)

    impression = str(summary.get("impression", "")).strip()
    if impression:
        if lines:
            lines.append("")
        lines.append(f"Impression: {impression}")
    return "\n".join(lines).strip() or "No report summary was returned."
