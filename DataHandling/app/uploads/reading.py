"""Memory-bounded helpers for multipart uploads."""

from __future__ import annotations

from typing import Protocol


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


async def read_upload_bounded(
    upload: AsyncReadable,
    *,
    max_bytes: int,
    chunk_size: int = 64 * 1024,
) -> bytes:
    """Read no more than ``max_bytes + 1`` bytes before rejecting an upload."""

    if max_bytes <= 0 or chunk_size <= 0:
        raise ValueError("Upload read limits must be positive")

    content = bytearray()
    while True:
        remaining = max_bytes - len(content)
        chunk = await upload.read(min(chunk_size, remaining + 1))
        if not chunk:
            return bytes(content)
        content.extend(chunk)
        if len(content) > max_bytes:
            raise ValueError("Upload exceeds the allowed byte limit")
