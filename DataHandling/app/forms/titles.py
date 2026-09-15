"""Deterministic display titles for persisted interview forms."""

from __future__ import annotations

from typing import Any, Mapping


TITLE_PREFIX = "Medical Interview: "
MAX_COMPLAINT_TITLE_CHARS = 30


def build_form_title(
    form_data: Mapping[str, Any] | None,
    *,
    empty_title: str = "Medical Interview Form",
) -> str:
    """Build the existing complaint-based title without an external AI call."""

    complaint: Any = ""
    if isinstance(form_data, Mapping):
        present_complaint = form_data.get("Present Complaint", {})
        if isinstance(present_complaint, Mapping):
            complaint = present_complaint.get("Primary Complaint", "")

    # Collapse whitespace so line breaks or repeated spaces cannot produce a
    # malformed title in form-selection views.
    normalized_complaint = " ".join(str(complaint or "").split())
    if not normalized_complaint:
        return empty_title

    complaint_excerpt = normalized_complaint[:MAX_COMPLAINT_TITLE_CHARS].rstrip()
    return f"{TITLE_PREFIX}{complaint_excerpt}"
