from pathlib import Path
import unittest

from app.uploads.policy import (
    detect_content_kind,
    validate_document_complexity,
    validate_upload_identity,
    validate_upload_quotas,
)
from app.uploads.reading import read_upload_bounded


class FakeUpload:
    def __init__(self, content):
        self.content = content
        self.offset = 0

    async def read(self, size=-1):
        if size < 0:
            size = len(self.content) - self.offset
        chunk = self.content[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


class UploadPolicyTests(unittest.TestCase):
    def test_detects_supported_magic_bytes(self):
        self.assertEqual(detect_content_kind(b"%PDF-1.7\n"), "pdf")
        self.assertEqual(detect_content_kind(b"\x89PNG\r\n\x1a\nrest"), "png")
        self.assertEqual(detect_content_kind(b"\xff\xd8\xffrest"), "jpeg")
        self.assertEqual(detect_content_kind(b"RIFF\x10\x00\x00\x00WEBPrest"), "webp")

    def test_rejects_unknown_or_spoofed_content(self):
        with self.assertRaisesRegex(ValueError, "not a supported"):
            validate_upload_identity(b"plain text", "report.pdf", "application/pdf")
        with self.assertRaisesRegex(ValueError, "extension"):
            validate_upload_identity(b"%PDF-1.7", "report.jpg", "image/jpeg")
        with self.assertRaisesRegex(ValueError, "content type"):
            validate_upload_identity(b"%PDF-1.7", "report.pdf", "image/png")

    def test_accepts_generic_mime_but_returns_detected_canonical_type(self):
        identity = validate_upload_identity(
            b"\xff\xd8\xffrest",
            "scan.jpeg",
            "application/octet-stream",
        )

        self.assertEqual(identity.kind, "jpeg")
        self.assertEqual(identity.content_type, "image/jpeg")

    def test_enforces_count_individual_and_aggregate_byte_limits(self):
        validate_upload_quotas(
            [4, 5],
            max_files=2,
            max_file_bytes=5,
            max_total_bytes=9,
        )
        with self.assertRaisesRegex(ValueError, "maximum"):
            validate_upload_quotas(
                [1, 1, 1], max_files=2, max_file_bytes=5, max_total_bytes=9
            )
        with self.assertRaisesRegex(ValueError, "per-file"):
            validate_upload_quotas(
                [6], max_files=2, max_file_bytes=5, max_total_bytes=9
            )
        with self.assertRaisesRegex(ValueError, "Combined"):
            validate_upload_quotas(
                [5, 5], max_files=2, max_file_bytes=5, max_total_bytes=9
            )

    def test_rejects_empty_files_and_invalid_policy(self):
        with self.assertRaisesRegex(ValueError, "Empty"):
            validate_upload_quotas(
                [0], max_files=1, max_file_bytes=1, max_total_bytes=1
            )
        with self.assertRaisesRegex(ValueError, "positive"):
            validate_upload_quotas(
                [1], max_files=0, max_file_bytes=1, max_total_bytes=1
            )

    def test_enforces_page_and_decoded_pixel_limits(self):
        validate_document_complexity(
            page_count=2,
            pixel_count=100,
            longest_side=10,
            max_pages=2,
            max_pixels=100,
            max_dimension=10,
        )
        with self.assertRaisesRegex(ValueError, "page limit"):
            validate_document_complexity(
                page_count=3,
                pixel_count=100,
                longest_side=10,
                max_pages=2,
                max_pixels=100,
                max_dimension=10,
            )
        with self.assertRaisesRegex(ValueError, "pixel limit"):
            validate_document_complexity(
                page_count=1,
                pixel_count=101,
                longest_side=11,
                max_pages=2,
                max_pixels=100,
                max_dimension=10,
            )

    def test_pdf_page_validation_does_not_invent_pixel_dimensions(self):
        validate_document_complexity(
            page_count=2,
            pixel_count=None,
            longest_side=None,
            max_pages=2,
            max_pixels=100,
            max_dimension=10,
        )

    def test_server_inspects_complete_content_before_s3_upload(self):
        server_source = (
            Path(__file__).resolve().parents[1] / "server.py"
        ).read_text(encoding="utf-8")

        inspection = server_source.index("inspect_report_upload,")
        upload = server_source.index("upload_bytes_to_s3,", inspection)
        self.assertLess(inspection, upload)


class UploadReadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_content_at_exact_limit(self):
        upload = FakeUpload(b"12345")
        self.assertEqual(
            await read_upload_bounded(upload, max_bytes=5, chunk_size=2),
            b"12345",
        )

    async def test_rejects_without_reading_the_entire_oversized_upload(self):
        upload = FakeUpload(b"x" * 100)
        with self.assertRaisesRegex(ValueError, "allowed byte limit"):
            await read_upload_bounded(upload, max_bytes=5, chunk_size=2)
        self.assertEqual(upload.offset, 6)

    async def test_rejects_invalid_read_limits(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            await read_upload_bounded(FakeUpload(b"x"), max_bytes=0)


if __name__ == "__main__":
    unittest.main()
