"""Pure upload identity and quota validation before storage/model processing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}
TYPE_POLICY = {
    "pdf": {
        "suffixes": {".pdf"},
        "content_types": {"application/pdf"},
    },
    "png": {
        "suffixes": {".png"},
        "content_types": {"image/png"},
    },
    "jpeg": {
        "suffixes": {".jpg", ".jpeg"},
        "content_types": {"image/jpeg", "image/jpg"},
    },
    "webp": {
        "suffixes": {".webp"},
        "content_types": {"image/webp"},
    },
}


@dataclass(frozen=True)
class UploadIdentity:
    kind: str
    content_type: str
    suffix: str


def detect_content_kind(data: bytes) -> str | None:
    """Detect supported report type from magic bytes, never the filename."""

    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_upload_identity(
    data: bytes,
    filename: str,
    declared_content_type: str | None,
) -> UploadIdentity:
    """Require content signature, extension, and meaningful MIME to agree."""

    kind = detect_content_kind(data)
    if kind is None:
        raise ValueError("File content is not a supported PDF, PNG, JPEG, or WebP report")

    policy = TYPE_POLICY[kind]
    suffix = Path(filename).suffix.lower()
    if suffix not in policy["suffixes"]:
        raise ValueError("File extension does not match detected content")

    normalized_type = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if (
        normalized_type not in GENERIC_CONTENT_TYPES
        and normalized_type not in policy["content_types"]
    ):
        raise ValueError("Declared content type does not match detected content")

    canonical_type = sorted(policy["content_types"])[0]
    if kind == "jpeg":
        canonical_type = "image/jpeg"
    return UploadIdentity(kind=kind, content_type=canonical_type, suffix=suffix)


def validate_upload_quotas(
    file_sizes: Iterable[int],
    *,
    max_files: int,
    max_file_bytes: int,
    max_total_bytes: int,
) -> None:
    """Validate file-count, per-file, and aggregate request limits."""

    if min(max_files, max_file_bytes, max_total_bytes) <= 0:
        raise ValueError("Upload limits must be positive")
    sizes = list(file_sizes)
    if not sizes:
        raise ValueError("At least one non-empty file is required")
    if len(sizes) > max_files:
        raise ValueError(f"A maximum of {max_files} files is allowed per request")
    if any(size <= 0 for size in sizes):
        raise ValueError("Empty files are not allowed")
    if any(size > max_file_bytes for size in sizes):
        raise ValueError("A file exceeds the per-file size limit")
    if sum(sizes) > max_total_bytes:
        raise ValueError("Combined files exceed the request size limit")


def validate_document_complexity(
    *,
    page_count: int,
    pixel_count: int | None,
    longest_side: int | None,
    max_pages: int,
    max_pixels: int,
    max_dimension: int,
) -> None:
    """Bound decoded PDF pages and image pixels before model processing."""

    if min(max_pages, max_pixels, max_dimension) <= 0:
        raise ValueError("Document complexity limits must be positive")
    if page_count <= 0:
        raise ValueError("Document contains no readable pages")
    if page_count > max_pages:
        raise ValueError(f"Document exceeds the {max_pages}-page limit")
    if pixel_count is not None:
        if pixel_count <= 0 or longest_side is None or longest_side <= 0:
            raise ValueError("Image dimensions are invalid")
        if pixel_count > max_pixels:
            raise ValueError("Image dimensions exceed the decoded-pixel limit")
        if longest_side > max_dimension:
            raise ValueError("Image dimensions exceed the side-length limit")
