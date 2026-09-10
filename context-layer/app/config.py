"""
Centralized configuration for the assessment test recommender.

Reads env vars only (via python-dotenv). No side effects — no DB connection,
no model load. Mirrors the customer-agent's app/config.py conventions.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


# ── MongoDB (SAME cluster as customer-agent / stance-dashboard) ─────────────
MONGO_URI: str | None = os.getenv("MONGO_URI")
MONGO_DB_NAME: str = os.getenv("MONGO_DB_NAME", "stance-dashboard")

# Read-only inputs (owned by other services)
COLL_CUSTOMER_INFO: str = os.getenv("COLL_CUSTOMER_INFO", "customer-info")
COLL_OBJECTIVE_ASSESSMENTS: str = os.getenv("COLL_OBJECTIVE_ASSESSMENTS", "objectiveAssessments")
COLL_VALD_DATA: str = os.getenv("COLL_VALD_DATA", "vald-exercise-data")

# Owned by this service
COLL_RECO: str = os.getenv("COLL_RECO", "customer_reco_form_data")
COLL_CONTEXT: str = os.getenv("COLL_CONTEXT", "assessment_context")

# The growing, file-backed pool of condition → tests. Source of truth for the
# deterministic pool; the agent appends learned tests here so coverage grows over
# time. Volume-mounted on the EC2 so writes persist across restarts.
POOL_FILE: str = os.getenv("POOL_FILE", "data/tests_pool.json")


# ── LLM ─────────────────────────────────────────────────────────────────────
# Master switch for the intelligent clinician: when true (+ a key) the agent reads
# the whole case and fills the form with judgment, grounded by the real catalog.
# When false, the deterministic keyword+template pipeline runs (no LLM cost).
USE_LLM: bool = True
# Legacy: LLM ranking within the deterministic pool (subsumed by USE_LLM).
USE_LLM_RANKER: bool = True
GEMINI_API_KEY: str | None = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


# ── HTTP / CORS ────────────────────────────────────────────────────────────
PORT: int = int(os.getenv("PORT", "8002"))
CORS_ALLOWED_ORIGINS: list[str] = [
    o.strip()
    for o in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "https://dashboard.stance.health,http://localhost:3000",
    ).split(",")
    if o.strip()
]

# Simple shared-secret auth for the REST endpoint. If unset, auth is disabled
# (fine for local dev; MUST be set in prod — endpoint carries PHI).
API_KEY: str | None = os.getenv("RECOMMENDER_API_KEY")


# ── Behavior ───────────────────────────────────────────────────────────────
# How many recommendations to surface by default.
MAX_TESTS: int = int(os.getenv("MAX_TESTS", "12"))
# Default priority for VALD tests pulled by body-part match.
VALD_DEFAULT_PRIORITY: int = int(os.getenv("VALD_DEFAULT_PRIORITY", "6"))
# Max net-new tests the LLM may propose beyond the catalogs (0 disables proposals).
MAX_PROPOSED_TESTS: int = int(os.getenv("MAX_PROPOSED_TESTS", "3"))
# Max non-catalog tests (VALD / suggested) listed in the subjective assessment text.
MAX_EXTRA_TESTS: int = int(os.getenv("MAX_EXTRA_TESTS", "6"))
# In-memory LRU size for active per-user contexts (bounds memory — no pileup).
CONTEXT_CACHE_SIZE: int = int(os.getenv("CONTEXT_CACHE_SIZE", "64"))
# Days of inactivity before a per-patient context / draft is auto-reaped in Mongo.
CONTEXT_TTL_DAYS: int = int(os.getenv("CONTEXT_TTL_DAYS", "30"))
