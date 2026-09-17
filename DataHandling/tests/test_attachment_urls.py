import unittest
from app.uploads.attachments import attachment_urls


class AttachmentURLTests(unittest.TestCase):
    def test_legacy_and_new_records_keep_only_uploaded_urls(self):
        self.assertEqual(attachment_urls([
            {"url": "https://example.org/a.pdf", "fileName": "a.pdf"},
            "https://example.org/b.png", {"url": "https://example.org/a.pdf"},
            {}, None, "", {"url": None},
        ]), ["https://example.org/a.pdf", "https://example.org/b.png"])

    def test_empty_attachments(self):
        self.assertEqual(attachment_urls(None), [])
