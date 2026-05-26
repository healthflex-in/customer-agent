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


# ── HTTP / CORS ────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS: list[str] = [
    "https://customerai.stance.health",
    "https://customer-agent-mu.vercel.app",
    "http://localhost:3000",
    "http://localhost:8080",
    "http://localhost:8000",
    "http://localhost:8081",
]


# ── Audio streaming ────────────────────────────────────────────────────────
AUDIO_RATE: int = 16000          # 16 kHz, Whisper's expected sample rate
AUDIO_SAMPLE_WIDTH: int = 2      # 16-bit PCM = 2 bytes per sample
MAX_WS_CHUNK_SIZE: int = 65536   # 64 KB per WebSocket message


# ── Form / attachment policy ──────────────────────────────────────────────
DEFAULT_FORM_ID: str = "FRM-01"

ALLOWED_ATTACHMENT_TYPES: dict[str, str] = {
    "mri": "MRI Scan",
    "report": "Clinical Report",
    "other": "Supporting Document",
}
MAX_ATTACHMENT_SIZE_MB: int = 15

UPLOAD_TRIGGER_PHRASE: str = (
    "do you have any mri, x-ray, ct scan, or blood reports"
)


# ── Filesystem ─────────────────────────────────────────────────────────────
TTS_CACHE_DIR: str = "tts_cache"
