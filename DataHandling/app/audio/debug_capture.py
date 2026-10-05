"""Opt-in, container-local audio capture for transcription debugging.

The normal production path keeps audio only in memory.  QA can explicitly turn
this module on to retain the original browser bytes beside the raw and cleaned
STT text.  Captures use random identifiers, are never served over HTTP, and are
pruned by age and total size so a test container cannot grow without bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid
from typing import Any


@dataclass(frozen=True)
class DebugAudioCapture:
    capture_id: str
    audio_path: Path
    transcript_path: Path
    cleaned_transcript_path: Path
    metadata_path: Path


def _audio_format(raw_bytes: bytes) -> tuple[str, str]:
    """Return ``(extension, mime_type)`` from browser audio magic bytes."""
    if raw_bytes[:4] == b"\x1aE\xdf\xa3":
        return ".webm", "audio/webm"
    if raw_bytes[:4] == b"OggS":
        return ".ogg", "audio/ogg"
    if raw_bytes[:4] == b"RIFF":
        return ".wav", "audio/wav"
    if raw_bytes[:3] == b"ID3" or raw_bytes[:2] == b"\xff\xfb":
        return ".mp3", "audio/mp3"
    # Match the STT service fallback: browser MediaRecorder normally sends
    # WebM, even when an intermediary omitted or changed the leading bytes.
    return ".webm", "audio/webm"


def _write_private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _write_private_text(path: Path, text: str) -> None:
    _write_private(path, str(text).encode("utf-8"))


def _capture_groups(directory: Path) -> list[tuple[float, int, list[Path]]]:
    grouped: dict[str, list[Path]] = {}
    for path in directory.iterdir():
        if path.is_file():
            grouped.setdefault(path.name.split(".", 1)[0], []).append(path)
    result = []
    for paths in grouped.values():
        existing = [path for path in paths if path.exists()]
        if not existing:
            continue
        result.append(
            (
                min(path.stat().st_mtime for path in existing),
                sum(path.stat().st_size for path in existing),
                existing,
            )
        )
    return sorted(result, key=lambda item: item[0])


def prune_debug_captures(
    directory: str | Path,
    *,
    retention_hours: int,
    max_total_bytes: int,
) -> None:
    """Delete expired captures, then oldest groups until within the size cap."""
    root = Path(directory)
    if not root.exists():
        return
    cutoff = time.time() - (retention_hours * 3600)
    groups = _capture_groups(root)
    for modified, _size, paths in groups:
        if modified >= cutoff:
            continue
        for path in paths:
            path.unlink(missing_ok=True)

    groups = _capture_groups(root)
    total = sum(size for _modified, size, _paths in groups)
    for _modified, size, paths in groups:
        if total <= max_total_bytes:
            break
        for path in paths:
            path.unlink(missing_ok=True)
        total -= size


def save_debug_audio(
    raw_audio_bytes: bytes,
    *,
    enabled: bool,
    directory: str | Path,
    retention_hours: int,
    max_total_bytes: int,
    metadata: dict[str, Any] | None = None,
) -> DebugAudioCapture | None:
    """Persist one original audio payload and return its paired capture paths."""
    if not enabled:
        return None
    if not raw_audio_bytes:
        raise ValueError("raw_audio_bytes cannot be empty")

    root = Path(directory)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    prune_debug_captures(
        root,
        retention_hours=retention_hours,
        max_total_bytes=max_total_bytes,
    )

    now = datetime.now(timezone.utc)
    capture_id = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid.uuid4().hex[:12]}"
    extension, mime_type = _audio_format(raw_audio_bytes)
    capture = DebugAudioCapture(
        capture_id=capture_id,
        audio_path=root / f"{capture_id}{extension}",
        transcript_path=root / f"{capture_id}.txt",
        cleaned_transcript_path=root / f"{capture_id}.clean.txt",
        metadata_path=root / f"{capture_id}.json",
    )
    _write_private(capture.audio_path, raw_audio_bytes)
    document = {
        "captureId": capture_id,
        "createdAt": now.isoformat(),
        "mimeType": mime_type,
        "audioFile": capture.audio_path.name,
        "byteLength": len(raw_audio_bytes),
        "transcriptionStatus": "pending",
        **(metadata or {}),
    }
    _write_private_text(
        capture.metadata_path,
        json.dumps(document, indent=2, default=str),
    )
    prune_debug_captures(
        root,
        retention_hours=retention_hours,
        max_total_bytes=max_total_bytes,
    )
    return capture


def update_debug_capture(
    capture: DebugAudioCapture | None,
    *,
    status: str,
    raw_transcript: str | None = None,
    cleaned_transcript: str | None = None,
    error_code: str | None = None,
) -> None:
    """Write paired STT output and update status metadata for a capture."""
    if capture is None:
        return
    if raw_transcript is not None:
        _write_private_text(capture.transcript_path, raw_transcript)
    if cleaned_transcript is not None:
        _write_private_text(capture.cleaned_transcript_path, cleaned_transcript)

    document: dict[str, Any] = {}
    if capture.metadata_path.exists():
        try:
            document = json.loads(capture.metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            document = {"captureId": capture.capture_id}
    document["transcriptionStatus"] = str(status)
    document["updatedAt"] = datetime.now(timezone.utc).isoformat()
    if raw_transcript is not None:
        document["rawTranscriptChars"] = len(raw_transcript)
        document["transcriptFile"] = capture.transcript_path.name
    if cleaned_transcript is not None:
        document["cleanedTranscriptChars"] = len(cleaned_transcript)
        document["cleanedTranscriptFile"] = capture.cleaned_transcript_path.name
    if error_code:
        document["errorCode"] = str(error_code)
    _write_private_text(
        capture.metadata_path,
        json.dumps(document, indent=2, default=str),
    )
