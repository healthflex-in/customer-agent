"""Small privacy boundary for operational logs and trace correlation."""

from __future__ import annotations

import hashlib
import hmac
import os


def pseudonymous_id(value: object, *, key: str | None = None) -> str | None:
    """Return a keyed, non-reversible correlation ID, or ``None`` without a key.

    A plain hash is intentionally not used: Mongo/Object IDs have predictable
    formats and can be enumerated. Deployments that need cross-event correlation
    must provide ``OBSERVABILITY_HASH_KEY`` from their secret manager.
    """

    raw = str(value or "").strip()
    secret = (key if key is not None else os.getenv("OBSERVABILITY_HASH_KEY", "")).strip()
    if not raw or not secret:
        return None
    digest = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"subject-{digest[:16]}"


def error_type(error: BaseException) -> str:
    """Expose an exception class for operations without logging its payload."""

    return type(error).__name__


def env_flag(name: str, *, default: bool = False) -> bool:
    """Parse an explicit boolean environment flag with a safe default."""

    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
