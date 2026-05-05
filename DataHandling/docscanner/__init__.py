"""
Shared doc-scanner utilities for summarizing medical reports.
"""

from .service import (
    summarize_report_from_bytes,
    summarize_report_from_path,
    summarize_report_from_url,
    summarize_multiple_reports,
)

__all__ = [
    "summarize_report_from_bytes",
    "summarize_report_from_path",
    "summarize_report_from_url",
    "summarize_multiple_reports",
]

