"""Form list, fetch, progress, and report-upload HTTP routes."""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import File, Form, HTTPException, Query, UploadFile

from app.config import (
    MAX_ATTACHMENT_FILES,
    MAX_ATTACHMENT_SIZE_MB,
    MAX_ATTACHMENT_TOTAL_MB,
    MAX_REPORT_TOTAL_PAGES,
    REPORT_JOB_MAX_BACKLOG,
)
from app.db.mongo import init_mongo, mongo
from app.db.ownership import build_owned_form_filter
from app.forms.lifecycle import is_completed_form
from app.forms.progress import (
    calculate_form_progress,
    calculate_section_completion_status,
)
from app.forms.repository import fetch_form_by_id, fetch_user_forms
from app.jobs.reports import build_report_job
from app.observability.logging import log as _log
from app.observability.privacy import error_type
from app.runtime.blocking import run_blocking
from app.uploads.policy import validate_upload_quotas
from app.uploads.reading import read_upload_bounded
from docscanner.service import inspect_report_upload
from upload.s3_client import (
    generate_object_key,
    is_s3_configured,
    upload_bytes_to_s3,
)


def register_form_routes(app) -> None:
    """Register form routes on the application itself."""


    @app.get("/api/users/{user_id}/forms")
    async def get_user_forms_endpoint(
        user_id: str,
    ):
        """Return all forms for a specific user."""
        try:
            forms = await run_blocking(fetch_user_forms, user_id)
            return {"forms": forms}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch user forms: {str(e)}"
            )


    @app.get("/api/forms/{form_id}")
    async def get_form_endpoint(
        form_id: str,
        userId: str = Query(..., description="User ID (required)"),
        attemptId: Optional[str] = Query(None, description="Form attempt ID"),
    ):
        """Return a specific form by form_id and userId."""
        try:
            form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
            if form is None:
                raise HTTPException(status_code=404, detail="Form not found")
            return {"form": form}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch form: {str(e)}"
            )


    @app.get("/api/forms/{form_id}/progress")
    async def get_form_progress_endpoint(
        form_id: str,
        userId: str = Query(..., description="User ID (required)"),
        attemptId: Optional[str] = Query(None, description="Form attempt ID"),
    ):
        """
        Return section completion status for a form.
        Sections are ordered with completed sections first, incomplete sections at the bottom.
        """
        try:
            form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
            if form is None:
                raise HTTPException(status_code=404, detail="Form not found")
        
            form_data = form.get("form_data", {})
            progress_data = calculate_section_completion_status(form_data)
        
            return progress_data
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to calculate form progress: {str(e)}"
            )


    @app.post("/api/forms/{form_id}/attachments")
    async def upload_form_attachment(
        form_id: str,
        files: List[UploadFile] = File(...),
        userId: str = Form(...),
        attemptId: Optional[str] = Form(None),
    ):
        """Upload multiple scans/reports to S3, generate combined summary, and auto-fill Reports section."""
        if not is_s3_configured():
            raise HTTPException(
                status_code=500,
                detail="S3 is not configured on the server.",
            )

        form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
        if not form:
            raise HTTPException(status_code=404, detail="Form not found.")

        # Enforce that each form remains bound to a single user.
        # Compare as strings to handle ObjectId vs string mismatches.
        if str(form.get("userId", "")) != str(userId):
            raise HTTPException(
                status_code=403,
                detail="Form does not belong to this user.",
            )

        if is_completed_form(form):
            raise HTTPException(
                status_code=409,
                detail=(
                    "This assessment has already been completed and is read-only. "
                    "A clinician must assign a new assessment before additional files can be uploaded."
                ),
            )

        if not files:
            raise HTTPException(status_code=400, detail="No files provided.")
        if len(files) > MAX_ATTACHMENT_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"A maximum of {MAX_ATTACHMENT_FILES} files is allowed per request.",
            )

        max_bytes = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
        max_total_bytes = MAX_ATTACHMENT_TOTAL_MB * 1024 * 1024
        validated_files = []
        total_pages = 0

        # Read and validate the complete request before creating any S3 objects.
        # This prevents a late invalid/oversized file from leaving partial uploads.
        for file_index, file in enumerate(files, start=1):
            filename = file.filename or "upload.bin"
            current_total = sum(len(item[0]) for item in validated_files)
            remaining_total = max_total_bytes - current_total
            try:
                if remaining_total <= 0:
                    raise ValueError("Combined files exceed the request size limit")
                read_limit = min(max_bytes, remaining_total)
                try:
                    file_bytes = await read_upload_bounded(file, max_bytes=read_limit)
                except ValueError as exc:
                    if remaining_total < max_bytes:
                        raise ValueError(
                            "Combined files exceed the request size limit"
                        ) from exc
                    raise ValueError("File exceeds the per-file size limit") from exc
                validate_upload_quotas(
                    [len(file_bytes)],
                    max_files=1,
                    max_file_bytes=max_bytes,
                    max_total_bytes=max_bytes,
                )
                running_total = current_total + len(file_bytes)
                if running_total > max_total_bytes:
                    raise ValueError("Combined files exceed the request size limit")
                inspection = await run_blocking(
                    inspect_report_upload,
                    file_bytes,
                    filename,
                    file.content_type,
                )
                total_pages += inspection.page_count
                if total_pages > MAX_REPORT_TOTAL_PAGES:
                    raise ValueError(
                        f"Combined reports exceed the {MAX_REPORT_TOTAL_PAGES}-page limit"
                    )
            except ValueError as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"File {file_index} was rejected: {exc}",
                ) from exc
            except Exception as exc:
                print(f"[upload_attachment] Content inspection failed: {error_type(exc)}")
                raise HTTPException(
                    status_code=400,
                    detail=f"File {file_index} is corrupt or cannot be decoded.",
                ) from exc

            validated_files.append(
                (file_bytes, filename, inspection.identity.content_type)
            )

        try:
            validate_upload_quotas(
                (len(item[0]) for item in validated_files),
                max_files=MAX_ATTACHMENT_FILES,
                max_file_bytes=max_bytes,
                max_total_bytes=max_total_bytes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if mongo.report_jobs_collection is None:
            await run_blocking(init_mongo)
        if mongo.report_jobs_collection is None:
            raise HTTPException(status_code=500, detail="Report queue is unavailable.")
        active_report_jobs = await run_blocking(
            mongo.report_jobs_collection.count_documents,
            {"status": {"$in": ["queued", "retry", "processing"]}},
            limit=REPORT_JOB_MAX_BACKLOG,
        )
        if active_report_jobs >= REPORT_JOB_MAX_BACKLOG:
            raise HTTPException(
                status_code=503,
                detail="Report processing is at capacity. Please retry later.",
            )

        report_objects = []
        attachment_records = []

        # Upload only after the entire batch has passed preflight validation.
        for file_bytes, filename, content_type in validated_files:
            key = generate_object_key(userId, form_id, filename)
        
            try:
                s3_url = await run_blocking(
                    upload_bytes_to_s3,
                    file_bytes,
                    key,
                    content_type,
                )
                print(f"[upload_attachment] Uploaded object ({len(file_bytes)} bytes)")
            except RuntimeError as exc:
                print(f"[upload_attachment] S3 upload failed: {error_type(exc)}")
                continue

            report_objects.append({"key": key, "filename": filename})
        
            attachment_records.append({
                "id": str(uuid.uuid4()),
                "type": "report",
                "label": "Medical Report",
                "fileName": filename,
                "url": s3_url,
                "uploadedAt": datetime.now(timezone.utc).isoformat(),
                "contentType": content_type,
            })

        if not report_objects:
            raise HTTPException(status_code=400, detail="No valid files were uploaded.")

        # ── Step 1: Save attachment records to MongoDB immediately ────────────────
        if mongo.customer_info_collection is None or mongo.report_jobs_collection is None:
            await run_blocking(init_mongo)
        if mongo.customer_info_collection is None or mongo.report_jobs_collection is None:
            raise HTTPException(status_code=500, detail="Database unavailable.")

        owned_form_filter = build_owned_form_filter(form, userId, form_id)
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        form_data = form.get("form_data", {})
        hist_diag = form_data.get("History & Diagnostics", {})
        processing_text = (
            f"Uploaded {len(attachment_records)} document(s). "
            "Report summary processing is in progress."
        )
        update_fields = {
            "reportProcessing": {
                "jobId": job_id,
                "status": "queued",
                "queuedAt": now,
            },
            "updatedAt": now,
        }
        from src.graph.pure_functions.summary import reports_with_verified_upload
        report_value = reports_with_verified_upload(hist_diag.get("Reports", ""))
        if not str(hist_diag.get("Reports", "")).strip():
            report_value += "; " + processing_text
        update_fields["form_data.History & Diagnostics.Reports"] = report_value
        form_data.setdefault("History & Diagnostics", {})["Reports"] = report_value

        from app.uploads.attachments import append_attachment_urls_expression
        await run_blocking(
            mongo.customer_info_collection.update_one,
            owned_form_filter,
            [{"$set": {
                **{key: {"$literal": value} for key, value in update_fields.items()},
                "attachments": append_attachment_urls_expression(attachment_records),
            }}],
        )
        print(f"[upload_attachment] Saved {len(attachment_records)} attachment(s) to MongoDB")

        try:
            job = build_report_job(
                job_id=job_id,
                user_id=owned_form_filter["userId"],
                form_id=form_id,
                objects=report_objects,
                now=now,
            )
            await run_blocking(
                mongo.report_jobs_collection.insert_one,
                job,
            )
        except Exception as exc:
            await run_blocking(
                mongo.customer_info_collection.update_one,
                {
                    "formId": form_id,
                    "userId": owned_form_filter["userId"],
                    "reportProcessing.jobId": job_id,
                },
                {
                    "$set": {
                        "reportProcessing.status": "enqueue_failed",
                        "reportProcessing.failedAt": datetime.now(timezone.utc),
                    }
                },
            )
            _log.error("report_job_enqueue_failed", error_type=error_type(exc))
            raise HTTPException(
                status_code=503,
                detail="Reports were uploaded, but summary processing could not be queued.",
            ) from exc

        updated_progress = calculate_form_progress(form_data)
        section_progress = calculate_section_completion_status(form_data)

        return {
            "attachments": attachment_records,
            "reports_filled": True,
            "ocr_status": "processing",
            "ocr_job_id": job_id,
            "form_data": form_data,
            "progress": updated_progress,
            "sectionProgress": section_progress,
        }
