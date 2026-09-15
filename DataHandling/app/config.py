"""
Centralized configuration for healthflex-agent.

All environment-variable reads and module-level constants previously sprinkled
through `server.py` live here. Importing this module has no side effects beyond
reading env vars (loaded via `python-dotenv`); it must not connect to anything
or load any model.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# ── MongoDB ────────────────────────────────────────────────────────────────
MONGO_URI: str | None = os.getenv("MONGO_URI")
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "stance-dashboard")
MONGO_USERS_COLLECTION: str = os.getenv("MONGO_USERS_COLLECTION", "users")
MONGO_CUSTOMER_INFO_COLLECTION: str = "customer-info"
MONGO_REPORT_JOBS_COLLECTION: str = os.getenv(
    "MONGO_REPORT_JOBS_COLLECTION", "customer-agent-report-jobs"
)
MONGO_CLINICAL_ESCALATIONS_COLLECTION: str = os.getenv(
    "MONGO_CLINICAL_ESCALATIONS_COLLECTION", "customer-agent-clinical-escalations"
)
MONGO_TLS_CA_FILE: str | None = os.getenv("MONGO_TLS_CA_FILE")


# ── Privacy / observability ────────────────────────────────────────────────
# Optional secret used only to correlate pseudonymous subjects across events.
OBSERVABILITY_HASH_KEY: str | None = os.getenv("OBSERVABILITY_HASH_KEY")


# ── HTTP / CORS ────────────────────────────────────────────────────────────
_DEFAULT_CORS_ALLOWED_ORIGINS = [
    "https://customerai.stance.health",
    "https://customer-agent-mu.vercel.app",
    "http://localhost:3000",
    "http://localhost:8080",
    "http://localhost:8000",
    "http://localhost:8081",
]
_extra_cors_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("CORS_EXTRA_ORIGINS", "").split(",")
    if origin.strip()
]
CORS_ALLOWED_ORIGINS: list[str] = list(
    dict.fromkeys([*_DEFAULT_CORS_ALLOWED_ORIGINS, *_extra_cors_origins])
)


# ── Audio streaming ────────────────────────────────────────────────────────
AUDIO_RATE: int = 16000          # 16 kHz, Whisper's expected sample rate
AUDIO_SAMPLE_WIDTH: int = 2      # 16-bit PCM = 2 bytes per sample
MAX_WS_CHUNK_SIZE: int = 65536   # 64 KB per WebSocket message
MAX_AUDIO_SESSION_MB: int = int(os.getenv("MAX_AUDIO_SESSION_MB", "10"))
MAX_AUDIO_SESSION_BYTES: int = MAX_AUDIO_SESSION_MB * 1024 * 1024
MAX_AUDIO_SESSION_SECONDS: int = int(os.getenv("MAX_AUDIO_SESSION_SECONDS", "180"))


# ── Form / attachment policy ──────────────────────────────────────────────
DEFAULT_FORM_ID: str = "FRM-01"


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value

ALLOWED_ATTACHMENT_TYPES: dict[str, str] = {
    "mri": "MRI Scan",
    "report": "Clinical Report",
    "other": "Supporting Document",
}
MAX_ATTACHMENT_SIZE_MB: int = _positive_int_env("MAX_ATTACHMENT_SIZE_MB", 15)
MAX_ATTACHMENT_FILES: int = _positive_int_env("MAX_ATTACHMENT_FILES", 5)
MAX_ATTACHMENT_TOTAL_MB: int = _positive_int_env("MAX_ATTACHMENT_TOTAL_MB", 25)
MAX_REPORT_PAGES_PER_FILE: int = _positive_int_env("MAX_REPORT_PAGES_PER_FILE", 5)
MAX_REPORT_TOTAL_PAGES: int = _positive_int_env("MAX_REPORT_TOTAL_PAGES", 5)
MAX_REPORT_IMAGE_PIXELS: int = _positive_int_env("MAX_REPORT_IMAGE_PIXELS", 25_000_000)
MAX_REPORT_IMAGE_DIMENSION: int = _positive_int_env("MAX_REPORT_IMAGE_DIMENSION", 8000)
MAX_BEDROCK_IMAGE_BYTES: int = _positive_int_env(
    "MAX_BEDROCK_IMAGE_BYTES",
    3_750_000,
)
REPORT_JOB_POLL_SECONDS: int = _positive_int_env("REPORT_JOB_POLL_SECONDS", 2)
REPORT_JOB_LEASE_SECONDS: int = _positive_int_env("REPORT_JOB_LEASE_SECONDS", 900)
REPORT_JOB_MAX_ATTEMPTS: int = _positive_int_env("REPORT_JOB_MAX_ATTEMPTS", 3)
REPORT_JOB_MAX_BACKLOG: int = _positive_int_env("REPORT_JOB_MAX_BACKLOG", 100)
REPORT_JOB_RETRY_BASE_SECONDS: int = _positive_int_env(
    "REPORT_JOB_RETRY_BASE_SECONDS", 30
)
REPORT_JOB_RETRY_MAX_SECONDS: int = _positive_int_env(
    "REPORT_JOB_RETRY_MAX_SECONDS", 900
)

if MAX_ATTACHMENT_TOTAL_MB < MAX_ATTACHMENT_SIZE_MB:
    raise ValueError("MAX_ATTACHMENT_TOTAL_MB must be at least MAX_ATTACHMENT_SIZE_MB")
if MAX_REPORT_TOTAL_PAGES < MAX_REPORT_PAGES_PER_FILE:
    raise ValueError("MAX_REPORT_TOTAL_PAGES must be at least MAX_REPORT_PAGES_PER_FILE")
if MAX_REPORT_TOTAL_PAGES > 5:
    raise ValueError("MAX_REPORT_TOTAL_PAGES cannot exceed the Nova 2 five-image limit")

UPLOAD_TRIGGER_PHRASE: str = (
    "do you have any mri, x-ray, ct scan, or blood reports"
)


# ── Filesystem ─────────────────────────────────────────────────────────────
