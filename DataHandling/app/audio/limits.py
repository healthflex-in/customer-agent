"""Pure validation helpers for bounded WebSocket audio sessions."""

from __future__ import annotations

from typing import Optional


def audio_limit_error(
    *,
    current_bytes: int,
    incoming_bytes: int,
    elapsed_seconds: float,
    max_total_bytes: int,
    max_duration_seconds: float,
    declared_duration_seconds: Optional[float] = None,
) -> Optional[str]:
    """Return a patient-safe error when an audio session exceeds its limits."""

    if current_bytes < 0 or incoming_bytes < 0:
        raise ValueError("Audio byte counts cannot be negative")
    if max_total_bytes <= 0 or max_duration_seconds <= 0:
        raise ValueError("Audio limits must be positive")

    if current_bytes + incoming_bytes > max_total_bytes:
        return "Audio recording is too large. Please record a shorter response."
    if elapsed_seconds > max_duration_seconds:
        return "Audio recording is too long. Please record a shorter response."
    if (
        declared_duration_seconds is not None
        and declared_duration_seconds > max_duration_seconds
    ):
        return "Audio recording is too long. Please record a shorter response."
    return None
