from fastapi import (
    FastAPI,
    WebSocket,
    HTTPException,
    Query,
    UploadFile,
    File,
    Form,
    Header,
)
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
# faster_whisper removed — STT now uses Google Cloud Speech-to-Text (see app/audio/stt.py)
import time
import os
import json
from datetime import datetime, timezone
from typing import List, Optional
import uuid
from dotenv import load_dotenv
load_dotenv()
from pymongo.collection import Collection
from bson import ObjectId

from upload.s3_client import (
    download_bytes_from_s3,
    generate_object_key,
    is_s3_configured,
    upload_bytes_to_s3,
)
from docscanner.service import inspect_report_upload

# Import HealthAgent and related functionalities
from src.llm.functionalities import HealthAgent

# Centralized configuration (env vars, constants, paths).
# Aliased to the legacy names used throughout this file so handler code is
# unchanged. New code should import directly from app.config.
from app.config import (
    MONGO_URI,
    MONGO_DB_NAME,
    MONGO_USERS_COLLECTION,
    MONGO_CUSTOMER_INFO_COLLECTION,
    MONGO_REPORT_JOBS_COLLECTION,
    MONGO_CLINICAL_ESCALATIONS_COLLECTION,
    MONGO_TLS_CA_FILE,
    AUTH_SIGNING_SECRET,
    AUTH_ISSUER,
    AUTH_AUDIENCE,
    AUTH_MAX_TOKEN_SECONDS,
    AUTH_CLOCK_SKEW_SECONDS,
    CORS_ALLOWED_ORIGINS,
    AUDIO_RATE as RATE,
    AUDIO_SAMPLE_WIDTH as SAMPLE_WIDTH,
    MAX_AUDIO_SESSION_BYTES,
    MAX_AUDIO_SESSION_SECONDS,
    DEFAULT_FORM_ID,
    ALLOWED_ATTACHMENT_TYPES,
    MAX_ATTACHMENT_SIZE_MB,
    MAX_ATTACHMENT_FILES,
    MAX_ATTACHMENT_TOTAL_MB,
    MAX_REPORT_TOTAL_PAGES,
    REPORT_JOB_POLL_SECONDS,
    REPORT_JOB_LEASE_SECONDS,
    REPORT_JOB_MAX_ATTEMPTS,
    REPORT_JOB_MAX_BACKLOG,
    REPORT_JOB_RETRY_BASE_SECONDS,
    REPORT_JOB_RETRY_MAX_SECONDS,
    UPLOAD_TRIGGER_PHRASE,
)
from app.db.connection import create_verified_mongo_client
from app.ai.models import MODEL_REGISTRY
from app.observability.ai_usage import PRICING_VERSION, gemini_usage, tracked_ai_call
from app.audio.limits import audio_limit_error
from app.runtime.blocking import run_blocking
from app.uploads.policy import validate_upload_quotas
from app.uploads.reading import read_upload_bounded
from app.jobs.reports import (
    FAILED as REPORT_JOB_FAILED,
    build_report_job,
    claim_report_job,
    ensure_report_job_indexes,
    mark_report_job_completed,
    mark_report_job_failed,
    renew_report_job_lease,
    render_report_summary,
)
from app.forms.titles import build_form_title
from app.forms.progress import (
    calculate_form_progress,
    calculate_section_completion_status,
)
from app.forms.lifecycle import (
    COMPLETED as FORM_COMPLETED,
    build_lifecycle_update_filter,
    ensure_form_lifecycle_ttl_index,
    resolve_form_lifecycle,
)
from app.forms.attempts import (
    add_attempt_write_scope,
    ensure_form_attempt_index,
    resolve_form_attempt_id,
)
from app.forms.prom import advance_tagged_turn, parse_structured_prom_answers
from app.forms.instruments import (
    apply_prom_answers,
    build_prom_snapshot,
    is_immutable_prom_question,
    questions_from_prom_snapshot,
)
from app.clinical.escalation import (
    assess_urgent_risk,
    ensure_escalation_indexes,
    record_escalation,
)
from app.ws.idempotency import RecentRequestWindow, RequestDecision
from app.observability.privacy import env_flag, error_type, pseudonymous_id
from app.security.access_tokens import (
    AuthConfigurationError,
    AuthContext,
    AuthorizationError,
    TokenValidationError,
    authorize_directory,
    authorize_patient,
    decode_access_token,
    extract_bearer_token,
)
# Pure stateless helpers. Aliased to legacy names used throughout this file.
from app.db.serializers import (
    normalize_user_id,
    serialize_datetime as _serialize_datetime,
    serialize_user,
)
from app.db.ownership import build_owned_form_filter
from app.db.queries import (
    build_user_form_queries,
    collection_namespace,
    empty_tagged_questions_result,
)

# Import MongoDB database connector
# MongoDB DISABLED - Commented out
# from db import MedicalInterviewDB

# ── Structured logging ──────────────────────────────────────────────────────
try:
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.contextvars.merge_contextvars,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    _log = structlog.get_logger()
    _HAS_STRUCTLOG = True
except ImportError:
    _HAS_STRUCTLOG = False
    class _FallbackLog:
        def info(self, event, **kw): print(f"[INFO] {event}", kw or "")
        def warning(self, event, **kw): print(f"[WARN] {event}", kw or "")
        def error(self, event, **kw): print(f"[ERROR] {event}", kw or "")
    _log = _FallbackLog()

# ── Prometheus metrics ───────────────────────────────────────────────────────
try:
    from prometheus_client import Gauge, make_asgi_app as _make_metrics_app
    from starlette_prometheus import PrometheusMiddleware
    WS_CONNECTIONS = Gauge('ws_active_connections', 'Active WebSocket sessions')
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    class _Noop:
        def inc(self, *a, **k): pass
        def dec(self, *a, **k): pass
        def observe(self, *a, **k): pass
        def labels(self, *a, **k): return self
    WS_CONNECTIONS = _Noop()

# ── FastAPI-native rate limiter (no external library needed) ─────────────────
from collections import defaultdict
class _RateLimiter:
    """Token-bucket rate limiter using FastAPI dependency injection."""
    def __init__(self, max_per_minute: int = 30):
        self._counts: dict = defaultdict(list)
        self._max = max_per_minute
    def check(self, key: str) -> bool:
        now = time.monotonic()
        calls = [t for t in self._counts[key] if now - t < 60]
        calls.append(now)
        self._counts[key] = calls
        return len(calls) <= self._max

_rate_limiter = _RateLimiter(max_per_minute=30)
_request_window = RecentRequestWindow(capacity=4096)


def _decode_configured_access_token(token: str) -> AuthContext:
    return decode_access_token(
        token,
        secret=AUTH_SIGNING_SECRET,
        issuer=AUTH_ISSUER,
        audience=AUTH_AUDIENCE,
        max_lifetime_seconds=AUTH_MAX_TOKEN_SECONDS,
        clock_skew_seconds=AUTH_CLOCK_SKEW_SECONDS,
    )


def _http_auth_context(authorization: Optional[str]) -> AuthContext:
    try:
        return _decode_configured_access_token(extract_bearer_token(authorization))
    except AuthConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured.",
        ) from exc
    except TokenValidationError as exc:
        raise HTTPException(
            status_code=401,
            detail="A valid bearer token is required.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _authorize_patient_http(
    context: AuthContext,
    patient_id: str,
    scope: str,
) -> None:
    try:
        authorize_patient(context, patient_id, scope)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail="Access denied.") from exc


def _authorize_directory_http(context: AuthContext) -> None:
    try:
        authorize_directory(context)
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail="Access denied.") from exc

# ── Active connections tracker for graceful shutdown ─────────────────────────
_active_ws_connections: set = set()
_shutting_down = False
_report_worker_task: asyncio.Task | None = None
_report_worker_stop: asyncio.Event | None = None

# ── Input sanitization ───────────────────────────────────────────────────────
import re as _re
def sanitize_patient_input(text: str) -> str:
    """Strip control chars, limit length, neutralize prompt injection."""
    if not text:
        return text
    text = text[:2000]
    text = _re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    _injection = [
        r'ignore\s+(all\s+)?(previous|prior)?\s*instructions?',
        r'you are now',  r'forget everything',  r'system prompt',  r'jailbreak',
        r'act as',  r'pretend (you are|to be)',
    ]
    for pat in _injection:
        text = _re.sub(pat, '[filtered]', text, flags=_re.IGNORECASE)
    return text.strip()

# ── FastAPI app with lifespan for graceful startup/shutdown ──────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance):
    global _shutting_down, _report_worker_task, _report_worker_stop
    _log.info("server_startup", service="healthflex-agent")
    _shutting_down = False
    _report_worker_stop = asyncio.Event()
    if report_jobs_collection is not None:
        _report_worker_task = asyncio.create_task(_report_worker_loop())
    yield
    # ── Graceful shutdown ───────────────────────────────────────────────────
    _shutting_down = True
    if _report_worker_stop is not None:
        _report_worker_stop.set()
    _log.info("server_shutdown_initiated", active_sessions=len(_active_ws_connections))
    # Notify active WebSocket sessions
    for ws in list(_active_ws_connections):
        try:
            await ws.send_text(json.dumps({
                "type": "system",
                "message": "Server restarting — your progress is saved. Reconnect in a few seconds."
            }))
        except Exception:
            pass
    # Wait up to 15s for sessions to wind down
    for _ in range(15):
        if not _active_ws_connections:
            break
        await asyncio.sleep(1)
    if _report_worker_task is not None:
        try:
            await asyncio.wait_for(_report_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _report_worker_task.cancel()
            await asyncio.gather(_report_worker_task, return_exceptions=True)
        _report_worker_task = None
    _log.info("server_shutdown_complete")

app = FastAPI(lifespan=lifespan, title="Healthflex Customer Agent", version="2.0.0")

if _HAS_PROMETHEUS:
    app.add_middleware(PrometheusMiddleware)
    metrics_app = _make_metrics_app()
    app.mount("/metrics", metrics_app)

# ── Agent thought stream: human-readable labels for each graph node ──────────
NODE_THOUGHTS: dict[str, dict[str, str]] = {
    "handle_first_turn": {
        "stage": "Starting Interview",
        "detail": "Initiating the consultation and reviewing your case...",
    },
    "extract_form_data": {
        "stage": "Extracting Information",
        "detail": "Reading your responses and organizing patient data...",
    },
    "validate_section": {
        "stage": "Validating Completeness",
        "detail": "Checking which sections have sufficient information...",
    },
    "advance_section": {
        "stage": "Advancing Assessment",
        "detail": "Moving to the next area of clinical assessment...",
    },
    "detect_correction": {
        "stage": "Detecting Corrections",
        "detail": "Checking if you're updating or correcting previous information...",
    },
    "apply_correction": {
        "stage": "Applying Corrections",
        "detail": "Updating your records with the corrected information...",
    },
    "generate_question": {
        "stage": "Formulating Question",
        "detail": "Preparing the next targeted clinical question...",
    },
    "generate_summary": {
        "stage": "Generating Summary",
        "detail": "Compiling a comprehensive summary of all your responses...",
    },
    "classify_summary_intent": {
        "stage": "Reviewing Feedback",
        "detail": "Understanding your response to the summary...",
    },
    "handle_summary_response": {
        "stage": "Processing Summary Response",
        "detail": "Handling your confirmation or requested changes...",
    },
    "handle_upload_response": {
        "stage": "Processing Upload",
        "detail": "Handling your document upload or file response...",
    },
}


async def _send_thought_update(
    websocket: WebSocket,
    completed_nodes: list[str],
    active_node: str | None = None,
) -> None:
    """Send a thought_update message to the frontend with the current thought list."""
    thoughts: list[dict[str, str]] = []
    for node in completed_nodes:
        info = NODE_THOUGHTS[node]
        thoughts.append({
            "stage": info["stage"],
            "detail": info["detail"],
            "status": "done",
        })
    if active_node:
        info = NODE_THOUGHTS[active_node]
        thoughts.append({
            "stage": info["stage"],
            "detail": info["detail"],
            "status": "active",
        })
    try:
        await websocket.send_text(json.dumps({
            "type": "thought_update",
            "thoughts": thoughts,
        }))
    except Exception:
        pass  # WebSocket closed — safe to ignore


# ── Langfuse LLM observability ───────────────────────────────────────────────
_langfuse_credentials_present = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)
_langfuse_content_approved = env_flag("LANGFUSE_CAPTURE_CONTENT", default=False)
_langfuse_enabled = _langfuse_credentials_present and _langfuse_content_approved
if _langfuse_enabled:
    try:
        from langfuse import get_client as _lf_get_client
        from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
        _langfuse = _lf_get_client()
        LlamaIndexInstrumentor().instrument()
        print("[langfuse] Content tracing explicitly enabled")
    except Exception as _lf_err:
        print(f"[langfuse] Initialization failed: {error_type(_lf_err)}")
        _langfuse_enabled = False
else:
    if _langfuse_credentials_present:
        print("[langfuse] Content tracing disabled by privacy default")
    else:
        print("[langfuse] Skipping — credentials not configured")
    _langfuse = None
# ─────────────────────────────────────────────────────────────────────────────

# Add CORS middleware to allow frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# STT now uses Google Cloud Speech-to-Text — no local model to load at startup.

# Initialize HealthAgent (kept for audio, summary title generation, and legacy fallback)
print("Initializing HealthAgent...")
health_agent = HealthAgent()
print("HealthAgent initialized")


# ── LangGraph interview graph ────────────────────────────────────────────────
# Build once at startup; each WebSocket turn calls graph.invoke(state).
_interview_graph = None
try:
    from src.graph.graph import build_interview_graph
    from src.graph.server_adapter import (
        build_graph_state,
        sync_client_state_from_graph,
        build_interview_state_from_graph,
        init_graph_state_in_client,
    )
    # save_customer_info is defined later in this file; wrap in a lambda to
    # capture it lazily so the graph is built before the function is defined.
    def _save_for_graph(user_id, form, section, form_id, chat_history=None,
                        preferred_doc_id=None, lifecycle_status=None,
                        attempt_id=None):
        # NOTE: preferred_doc_id must be passed in by the caller — this function
        # runs at module scope (and inside background threads) where the per-
        # connection `client_state` is NOT in scope. Referencing it here raised
        # NameError on every call, silently dropping the FRM-01 intake save.
        return save_customer_info(user_id=user_id, form_data=form,
                                  current_section=section, form_id=form_id,
                                  chat_history=chat_history,
                                  preferred_doc_id=preferred_doc_id,
                                  lifecycle_status=lifecycle_status,
                                  attempt_id=attempt_id)

    # In-memory checkpointer — MongoDB is the persistent source of truth.
    from src.graph.graph import create_checkpointer as _create_checkpointer
    _checkpointer = _create_checkpointer()

    # Initialize reasoning-grade LLM (gemini-2.5-flash) for form extraction.
    # Kept separate from the main LLM (flash-lite) so only extraction pays
    # for the more capable model.
    _reasoning_llm_complete = None
    try:
        from src.llm.utils import init_reasoning_llm
        # Try every possible env var name for the Gemini API key
        _api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
                    or os.getenv("GOOGLE_GEMINI_API_KEY"))
        print(f"[graph] Reasoning LLM — API key present: {'yes' if _api_key else 'NO'}")
        if not _api_key:
            # Last resort: pull from health_agent if it has it cached
            try:
                from src.llm.utils import load_gemini_key
                _api_key = load_gemini_key()
                print(f"[graph] Reasoning LLM — loaded key from config: {'yes' if _api_key else 'NO'}")
            except Exception:
                pass
        if _api_key:
            # Use google.genai SDK directly so we can set thinking_budget=0.
            # gemini-2.5-flash has thinking ON by default (dynamic budget) which
            # adds several seconds of latency for every extraction call.
            # thinking_budget=0 disables it, giving flash-2.0 speed with 2.5 quality.
            try:
                from google import genai as _genai_r
                from google.genai import types as _genai_r_types
                _r_client = _genai_r.Client(api_key=_api_key)
                _r_model_name = MODEL_REGISTRY.reasoning
                _r_gen_config = _genai_r_types.GenerateContentConfig(
                    temperature=0.1,
                    thinking_config=_genai_r_types.ThinkingConfig(thinking_budget=0),
                )
                def _reasoning_llm_complete(prompt: str) -> str:
                    resp = tracked_ai_call(
                        provider="google_genai",
                        model=_r_model_name,
                        operation="form_extraction",
                        call=lambda: _r_client.models.generate_content(
                            model=_r_model_name,
                            contents=prompt,
                            config=_r_gen_config,
                        ),
                        usage_extractor=gemini_usage,
                    )
                    return (resp.text or "").strip()
                print(f"[graph] Reasoning LLM ({_r_model_name}, thinking=off) initialized for form extraction")
            except Exception as _r_sdk_err:
                print(f"[graph] Direct SDK init failed with {error_type(_r_sdk_err)}; using fallback")
                _r_llm = init_reasoning_llm(_api_key)
                def _reasoning_llm_complete(prompt: str) -> str:
                    resp = tracked_ai_call(
                        provider="google_genai",
                        model=_r_model_name,
                        operation="form_extraction",
                        call=lambda: _r_llm.complete(prompt),
                        usage_extractor=gemini_usage,
                    )
                    text = getattr(resp, 'text', None) or str(resp)
                    return text.strip()
                print(f"[graph] Reasoning LLM ({_r_model_name}) initialized for form extraction")
        else:
            print("[graph] WARNING: No API key — reasoning LLM disabled, using flash-lite fallback")
    except Exception as _r_err:
        print(f"[graph] Reasoning LLM initialization failed: {error_type(_r_err)}")

    _interview_graph = build_interview_graph(
        llm_complete=health_agent.llm_complete,
        system_prompt=health_agent.system_prompt,
        save_customer_info_fn=_save_for_graph,
        checkpointer=_checkpointer,
        reasoning_llm=_reasoning_llm_complete,
    )
    print("[graph] LangGraph interview graph initialized")
except Exception as _graph_err:
    print(f"[graph] LangGraph initialization failed with {error_type(_graph_err)}; using fallback")
    _interview_graph = None

# MongoDB connection handles (populated by init_mongo()).
mongo_client = None
users_collection: Optional[Collection] = None
customer_info_collection: Optional[Collection] = None
tagged_questions_collection: Optional[Collection] = None
report_jobs_collection: Optional[Collection] = None
clinical_escalations_collection: Optional[Collection] = None


# normalize_user_id moved to app.db.serializers (imported above).


def init_mongo():
    """Initialize MongoDB client for user directory lookups and customer info."""
    global mongo_client, users_collection, customer_info_collection, tagged_questions_collection, report_jobs_collection, clinical_escalations_collection

    if not MONGO_URI:
        print("MONGO_URI not set. User suggestions and customer info endpoints will be disabled.")
        return

    try:
        mongo_client = create_verified_mongo_client(
            MONGO_URI,
            ca_file=MONGO_TLS_CA_FILE,
        )
        mongo_client.admin.command("ping")
        db = mongo_client[MONGO_DB_NAME]
        users_collection = db[MONGO_USERS_COLLECTION]
        customer_info_collection = db[MONGO_CUSTOMER_INFO_COLLECTION]
        tagged_questions_collection = db["tagged-questions"]
        report_jobs_collection = db[MONGO_REPORT_JOBS_COLLECTION]
        clinical_escalations_collection = db[MONGO_CLINICAL_ESCALATIONS_COLLECTION]

        # A formId identifies a questionnaire; attemptId identifies one intake.
        # This migration preserves legacy records while allowing repeat intakes.
        ensure_form_attempt_index(customer_info_collection)

        # Drafts alone receive expiresAt. Answered/completed records omit it,
        # and the partial filter provides defense if stale data is present.
        ensure_form_lifecycle_ttl_index(customer_info_collection)
        ensure_report_job_indexes(report_jobs_collection)
        ensure_escalation_indexes(clinical_escalations_collection)

        print(
            f"Connected to MongoDB collections: {MONGO_DB_NAME}.{MONGO_USERS_COLLECTION}, {MONGO_DB_NAME}.{MONGO_CUSTOMER_INFO_COLLECTION}"
        )
    except Exception as e:
        mongo_client = None
        users_collection = None
        customer_info_collection = None
        tagged_questions_collection = None
        report_jobs_collection = None
        clinical_escalations_collection = None
        print(f"MongoDB connection failed: {error_type(e)}")


async def _process_report_job(job: dict, worker_id: str) -> None:
    """Process one leased report job and atomically publish only the latest result."""

    from docscanner.service import summarize_multiple_reports

    try:
        max_total_bytes = MAX_ATTACHMENT_TOTAL_MB * 1024 * 1024
        file_data_list = []
        downloaded_bytes = 0
        for item in job.get("objects", []):
            remaining = max_total_bytes - downloaded_bytes
            if remaining <= 0:
                raise ValueError("Report job exceeds the aggregate download limit")
            content = await run_blocking(
                download_bytes_from_s3,
                item["key"],
                remaining,
            )
            downloaded_bytes += len(content)
            file_data_list.append((content, item["filename"]))
        if not file_data_list:
            raise ValueError("Report job contains no objects")

        summary = await run_blocking(summarize_multiple_reports, file_data_list)
        if not summary or summary.get("error"):
            raise RuntimeError("Report summarizer returned no usable result")
        lease_is_current = await run_blocking(
            renew_report_job_lease,
            report_jobs_collection,
            job["_id"],
            worker_id=worker_id,
            lease_seconds=REPORT_JOB_LEASE_SECONDS,
        )
        if not lease_is_current:
            _log.info("report_job_lease_lost", job_id=job["_id"])
            return
        reports_text = render_report_summary(summary, len(file_data_list))
        now = datetime.now(timezone.utc)
        result = await run_blocking(
            customer_info_collection.update_one,
            {
                "formId": job["formId"],
                "userId": job["userId"],
                "reportProcessing.jobId": job["_id"],
            },
            {
                "$set": {
                    "form_data.History & Diagnostics.Reports": reports_text,
                    "reportProcessing.status": "completed",
                    "reportProcessing.completedAt": now,
                    "updatedAt": now,
                }
            },
        )
        if result.matched_count == 0:
            _log.info("report_job_result_superseded", job_id=job["_id"])
        await run_blocking(
            mark_report_job_completed,
            report_jobs_collection,
            job["_id"],
            worker_id=worker_id,
        )
        _log.info("report_job_completed", job_id=job["_id"])
    except Exception as exc:
        status = await run_blocking(
            mark_report_job_failed,
            report_jobs_collection,
            job,
            worker_id=worker_id,
            error_name=error_type(exc),
            max_attempts=REPORT_JOB_MAX_ATTEMPTS,
            retry_base_seconds=REPORT_JOB_RETRY_BASE_SECONDS,
            retry_max_seconds=REPORT_JOB_RETRY_MAX_SECONDS,
        )
        _log.warning(
            "report_job_attempt_failed",
            job_id=job.get("_id"),
            status=status,
            error_type=error_type(exc),
        )
        if status == REPORT_JOB_FAILED and customer_info_collection is not None:
            await run_blocking(
                customer_info_collection.update_one,
                {
                    "formId": job["formId"],
                    "userId": job["userId"],
                    "reportProcessing.jobId": job["_id"],
                },
                {
                    "$set": {
                        "reportProcessing.status": "failed",
                        "reportProcessing.failedAt": datetime.now(timezone.utc),
                    }
                },
            )


async def _report_worker_loop() -> None:
    """Claim durable jobs one at a time; leases recover work after a crash."""

    worker_id = f"{os.getpid()}-{uuid.uuid4().hex}"
    while _report_worker_stop is not None and not _report_worker_stop.is_set():
        try:
            job = await run_blocking(
                claim_report_job,
                report_jobs_collection,
                worker_id=worker_id,
                lease_seconds=REPORT_JOB_LEASE_SECONDS,
                max_attempts=REPORT_JOB_MAX_ATTEMPTS,
            )
            if job:
                await _process_report_job(job, worker_id)
                continue
        except Exception as exc:
            _log.warning("report_worker_poll_failed", error_type=error_type(exc))
        try:
            await asyncio.wait_for(
                _report_worker_stop.wait(),
                timeout=REPORT_JOB_POLL_SECONDS,
            )
        except asyncio.TimeoutError:
            pass


# _serialize_datetime and serialize_user moved to app.db.serializers (imported above).


_PROM_SCALE_MAP = {
    "prom_ohs":    "Oxford Hip Score (OHS)",
    "prom_oss":    "Oxford Shoulder Score (OSS)",
    "prom_phq9":   "PHQ-9 (Depression)",
    "prom_gad7":   "GAD-7 (Anxiety)",
    "prom_nps":    "Numeric Pain Scale (NPS)",
    "prom_rmdq":   "RMDQ (Back Disability)",
    "prom_koos":   "KOOS (Knee)",
    "prom_dash":   "DASH (Arm/Shoulder/Hand)",
    "prom_odi":    "Oswestry Disability Index (ODI)",
    "prom_whoqol": "WHOQOL (Quality of Life)",
}


def _prom_field_label(suffix: str) -> str:
    return suffix.replace("_", " ").title()


def _build_prom_template_from_ids(question_ids: list) -> dict:
    """Group question IDs by PROM scale to build a structured form template."""
    template: dict = {}
    for qid in question_ids:
        scale_name = "Other Assessments"
        suffix = qid
        for prefix, name in _PROM_SCALE_MAP.items():
            if qid.startswith(prefix + "_"):
                scale_name = name
                suffix = qid[len(prefix) + 1:]
                break
        if scale_name not in template:
            template[scale_name] = {}
        template[scale_name][_prom_field_label(suffix)] = ""
    return template


def _prom_scale_is_answered(val) -> bool:
    """Return True only when a PROM scale value has at least one real answer.
    Empty dicts (skeleton placeholders) and empty strings both return False."""
    if isinstance(val, dict):
        return any(str(v).strip() for v in val.values() if v)
    return bool(val and str(val).strip())


def _collapse_prom_form(form: dict) -> dict:
    """
    Collapse nested PROM form {scale: {question: answer, ...}} into
    {scale: "Question: answer; Question: answer"} — one line per scale.
    Empty fields are omitted. Non-dict sections passed through unchanged.
    """
    collapsed = {}
    for section, data in form.items():
        if isinstance(data, dict):
            parts = [
                f"{field}: {value}"
                for field, value in data.items()
                if value and str(value).strip()
            ]
            collapsed[section] = "; ".join(parts)
        else:
            collapsed[section] = data
    return collapsed


def fetch_tagged_questions(user_id: str, form_id: str) -> tuple:
    """
    Return (resolved_texts, prom_form_template, source_document_id) for the
    exact (user_id, form_id). Returns ([], {}, None) if nothing is found.
    """
    if tagged_questions_collection is None:
        return empty_tagged_questions_result()
    try:
        queries = build_user_form_queries(user_id, form_id)

        doc = None
        _from_customer_info = False
        for q in queries:
            doc = tagged_questions_collection.find_one(q)
            if doc:
                break

        # Fallback: check customer_info_collection for template docs that embed the
        # questions array directly (new template format from the clinic admin system)
        if not doc and customer_info_collection is not None:
            for q in queries:
                doc = customer_info_collection.find_one(q)
                if doc and doc.get("questions"):
                    _from_customer_info = True
                    print("[tagged-questions] Found embedded questions in customer-info")
                    break
                doc = None

        if not doc:
            print("[tagged-questions] No matching document found")
            return empty_tagged_questions_result()

        raw_questions = doc.get("questions", [])
        if not isinstance(raw_questions, list):
            return empty_tagged_questions_result()

        # Defaults are limited to non-clinical/custom questions. Published PROM
        # options cannot be safely inferred from an ID prefix: individual items
        # can have different anchors and directionality.
        _PROM_DEFAULT_METAS: dict = {
            "biz_feedback": {"type": "single_choice", "options": ["Yes, happy to help", "No, thank you"]},
        }

        # Hard overrides: DB value is wrong for these question IDs
        _QUESTION_ID_META_OVERRIDES: dict = {
            "biz_feedback_nps": {"type": "scale", "options": None},
        }

        def _infer_meta(qid: str, db_type: str, db_options) -> tuple:
            """Return (type, options), inferring from question ID prefix when not in DB."""
            if qid in _QUESTION_ID_META_OVERRIDES:
                m = _QUESTION_ID_META_OVERRIDES[qid]
                return m["type"], m["options"]
            if db_type and db_type != "text" and db_options:
                return db_type, db_options
            for prefix, meta in _PROM_DEFAULT_METAS.items():
                if qid.startswith(prefix):
                    return meta["type"], meta["options"]
            return db_type or "text", db_options

        # Split questions into three buckets:
        # 1. full_embedded: dict with text + type already present (new template format)
        # 2. id_only: dict with only an ID → needs question-bank lookup
        # 3. plain: raw strings or dicts with only text
        full_embedded = []   # (qid, q_dict) — text/type/options already available
        question_ids = []    # IDs that need question-bank lookup
        plain_texts = []     # raw text strings

        for q in raw_questions:
            if isinstance(q, str):
                plain_texts.append(q)
            elif isinstance(q, dict):
                qid = q.get("questionId") or q.get("id") or q.get("question_id")
                has_text = bool(q.get("text"))
                has_type = bool(q.get("type") and q.get("type") != "text")
                if qid and has_text and has_type:
                    # Embedded format: full question data already available
                    full_embedded.append((qid, q))
                elif qid:
                    question_ids.append(qid)
                elif has_text:
                    plain_texts.append(q["text"])

        # Resolve ID-only questions from question-bank
        id_to_doc: dict = {}
        if question_ids:
            try:
                qb = tagged_questions_collection.database["question-bank"]
                bank_docs = qb.find(
                    {"id": {"$in": question_ids}},
                    {
                        "id": 1,
                        "text": 1,
                        "type": 1,
                        "options": 1,
                        "instrumentVersion": 1,
                        "instrument_version": 1,
                        "_id": 0,
                    },
                )
                id_to_doc = {d["id"]: d for d in bank_docs if d.get("text")}
            except Exception as e:
                print(f"[tagged-questions] Question-ID resolution failed: {error_type(e)}")

        # Build resolved list (preserve order: plain texts first, then all question dicts)
        resolved = [{"text": t, "type": "text", "options": None, "question_id": None} for t in plain_texts]

        # Embedded questions — use their own text/type/options directly
        all_question_ids_for_template = []
        for qid, q in full_embedded:
            _type, _options = _infer_meta(qid, q.get("type", "text"), q.get("options"))
            resolved.append({
                "text": q["text"],
                "type": _type,
                "options": _options,
                "question_id": qid,
                "instrument_version": q.get("instrumentVersion") or q.get("instrument_version"),
            })
            all_question_ids_for_template.append(qid)

        # ID-only questions — use question-bank data
        for qid in question_ids:
            all_question_ids_for_template.append(qid)
            if qid in id_to_doc:
                d = id_to_doc[qid]
                _type, _options = _infer_meta(qid, d.get("type", "text"), d.get("options"))
                resolved.append({
                    "text": d["text"],
                    "type": _type,
                    "options": _options,
                    "question_id": qid,
                    "instrument_version": d.get("instrumentVersion") or d.get("instrument_version"),
                })
            else:
                print(f"[tagged-questions] Warning: '{qid}' not found in question-bank")

        # Build prom_template from resolved questions using full question text as field labels.
        # This ensures the MongoDB skeleton and saved answers both use the full question text
        # as the key, giving a human-readable form_data structure.
        prom_template: dict = {}
        for _r in resolved:
            if not isinstance(_r, dict):
                continue
            _r_qid  = _r.get("question_id") or ""
            _r_text = _r.get("text", "").strip()
            if not _r_text or not _r_qid:
                continue
            _r_scale = "Other Assessments"
            for _r_pfx, _r_nm in _PROM_SCALE_MAP.items():
                if _r_qid.startswith(_r_pfx + "_"):
                    _r_scale = _r_nm
                    break
            if _r_scale not in prom_template:
                prom_template[_r_scale] = {}
            prom_template[_r_scale][_r_text] = ""

        # Re-sort resolved so all questions within a scale are contiguous.
        # batch_tagged_questions uses positional slicing keyed on prom_template field counts,
        # so the order of resolved MUST match the order of scales in prom_template.
        _scale_order = {s: i for i, s in enumerate(prom_template.keys())}
        def _scale_rank(item):
            if not isinstance(item, dict):
                return len(_scale_order)
            _qid = item.get("question_id") or ""
            for _pfx, _nm in _PROM_SCALE_MAP.items():
                if _qid.startswith(_pfx + "_"):
                    return _scale_order.get(_nm, len(_scale_order))
            return _scale_order.get("Other Assessments", len(_scale_order))
        resolved.sort(key=_scale_rank)

        _source_doc_id = str(doc.get("_id", "")) if _from_customer_info and doc else None
        print(f"[tagged-questions] Resolved {len(resolved)} questions and {len(prom_template)} PROM scales")
        return resolved, prom_template, _source_doc_id
    except Exception as e:
        print(f"[tagged-questions] Fetch failed: {error_type(e)}")
    return empty_tagged_questions_result()


_SCALE_RESPONSE_OPTIONS: dict = {
    "Oxford Hip Score": "(options: none / very mild / mild / moderate / severe)",
    "OHS": "(options: none / very mild / mild / moderate / severe)",
    "Oxford Shoulder Score": "(options: none / very mild / mild / moderate / severe)",
    "OSS": "(options: none / very mild / mild / moderate / severe)",
    "PHQ": "(options: not at all / several days / more than half the days / nearly every day)",
    "GAD": "(options: not at all / several days / more than half the days / nearly every day)",
    "NPS": "(0 = no pain, 10 = worst possible pain)",
    "RMDQ": "(please answer yes or no)",
    "Roland": "(please answer yes or no)",
}

_SCALE_INTROS: dict = {
    "Oxford Hip Score": "Now let me ask about your hip over the past 4 weeks.",
    "OHS": "Now let me ask about your hip over the past 4 weeks.",
    "Oxford Shoulder Score": "Now let me ask about your shoulder over the past 4 weeks.",
    "OSS": "Now let me ask about your shoulder over the past 4 weeks.",
    "PHQ": "I'd like to ask a few questions about your mood and mental well-being.",
    "GAD": "Let me ask a few questions about how you've been feeling emotionally.",
    "NPS": "Quick pain check —",
    "RMDQ": "Now let me ask about how your back pain has affected your daily activities.",
    "Roland": "Now let me ask about how your back pain has affected your daily activities.",
}


def batch_tagged_questions(questions: list, llm_complete, form_template: dict = None) -> list:
    """
    Combine ALL PROM questions into a single multi_answer turn so the patient
    sees the entire assessment at once.

    Questions are ordered by PROM scale (using question_id prefix matching) and
    the resulting turn includes a `question_scales` list so the frontend can
    render section headers (e.g. "Oxford Hip Score", "PHQ-9").
    """
    if not questions:
        return []

    # --- Group by scale via question_id prefix ---
    if form_template:
        scale_order = list(form_template.keys())
    else:
        scale_order = []

    buckets: dict = {}
    for q in questions:
        q_dict = q if isinstance(q, dict) else {"text": q, "type": "text", "options": None, "question_id": None}
        qid = q_dict.get("question_id") or ""
        assigned = "Other Assessments"
        for pfx, name in _PROM_SCALE_MAP.items():
            if qid.startswith(pfx + "_"):
                assigned = name
                break
        buckets.setdefault(assigned, []).append(q_dict)

    # Preserve scale ordering from form_template
    seen: set = set()
    scale_groups: list = []
    for sname in scale_order:
        if sname in buckets and sname not in seen:
            scale_groups.append((sname, buckets[sname]))
            seen.add(sname)
    for sname, qs in buckets.items():
        if sname not in seen:
            scale_groups.append((sname, qs))

    # Flatten all questions into a single ordered list with scale labels
    all_ids, all_texts, all_opts, all_types, all_scales = [], [], [], [], []
    for scale_name, qs in scale_groups:
        for c in qs:
            all_ids.append(c.get("question_id"))
            all_texts.append(c.get("text", ""))
            all_opts.append(c.get("options"))
            all_types.append(c.get("type", "text"))
            all_scales.append(scale_name)

    combined_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(all_texts))
    print(f"[tagged-questions] {len(questions)} questions → 1 combined turn")
    return [{
        "text": combined_text,
        "type": "multi_answer",
        "options": None,
        "question_id": None,
        "question_ids": all_ids,
        "questions": all_texts,
        "question_options": all_opts,
        "question_types": all_types,
        "question_scales": all_scales,
    }]


def personalize_questions(raw_questions: list, patient_context: dict, llm_complete) -> list:
    """
    Rewrite only non-PROM custom questions for this patient's situation.

    Uses FRM-01 intake data (onset, body part, severity, duration) to replace
    generic template phrasing ("past 4 weeks", "your joint") with patient-accurate
    language ("past 3 days", "your right knee").  If a question touches something
    already well-documented in the patient's history, it is reframed as a follow-up
    ("You mentioned X last time — has that changed?").

    Falls back to raw templates on any error so the interview is never blocked.
    """
    if not raw_questions:
        return raw_questions

    import json as _json

    # Build a compact clinical summary from FRM-01 (key facts only, skip empty / N/A values)
    _na_markers = {"not applicable", "n/a", "na", "none", "general visit"}
    facts = []
    for section, fields in patient_context.items():
        if isinstance(fields, dict):
            for field, value in fields.items():
                v = str(value).strip()
                if v and not any(v.lower().startswith(m) for m in _na_markers):
                    facts.append(f"{field}: {v}")
        elif isinstance(fields, str) and fields.strip():
            facts.append(f"{section}: {fields.strip()}")

    if not facts:
        print("[personalize-questions] No usable patient facts; using raw templates")
        return raw_questions

    # Published/clinical PROM wording and time windows are part of the instrument
    # definition. They are never sent to an LLM for rewriting. A stored snapshot
    # is also frozen so a resume cannot silently adopt later question-bank edits.
    editable_indexes = [
        index
        for index, question in enumerate(raw_questions)
        if not is_immutable_prom_question(question)
        and not (isinstance(question, dict) and question.get("definition_frozen"))
    ]
    if not editable_indexes:
        print("[personalize-questions] Clinical definitions are immutable; skipping personalization")
        return raw_questions

    raw_texts = [
        raw_questions[index]["text"]
        if isinstance(raw_questions[index], dict)
        else raw_questions[index]
        for index in editable_indexes
    ]

    patient_summary = "\n".join(facts[:30])  # Cap to keep prompt concise
    questions_json = _json.dumps(raw_texts)

    prompt = (
        "You are a clinical assistant personalizing assessment questions for a specific patient.\n\n"
        "PATIENT INTAKE HISTORY (FRM-01):\n"
        f"{patient_summary}\n\n"
        f"CUSTOM QUESTION TEMPLATES ({len(raw_texts)} questions):\n"
        f"{questions_json}\n\n"
        "RULES:\n"
        "1. Replace 'past 4 weeks' (or any generic time window) with the patient's actual "
        "duration if documented (e.g. 'past 5 days', 'past 2 months'). If duration is unknown, "
        "remove the time reference entirely.\n"
        "2. Replace generic body-part language ('your joint', 'the affected area') with the "
        "patient's specific condition (e.g. 'your right hip', 'your lower back').\n"
        "3. If a question asks about something already well-captured in the intake history, "
        "reframe it as a follow-up: 'You mentioned [X] — has that changed recently?'\n"
        "4. Do NOT change the clinical meaning or intent of any question.\n"
        "5. Keep exactly the same number of questions in the same order.\n"
        "6. Keep each question concise — one sentence where possible.\n\n"
        f"Return ONLY a valid JSON array of exactly {len(raw_texts)} strings. No other text."
    )

    try:
        raw = llm_complete(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        personalized_texts = _json.loads(raw.strip())
        if isinstance(personalized_texts, list) and len(personalized_texts) == len(editable_indexes):
            print(f"[personalize-questions] Personalized {len(personalized_texts)} questions using patient history")
            result = list(raw_questions)
            for output_index, source_index in enumerate(editable_indexes):
                question = raw_questions[source_index]
                result[source_index] = (
                    {**question, "text": personalized_texts[output_index]}
                    if isinstance(question, dict)
                    else personalized_texts[output_index]
                )
            return result
        print(f"[personalize-questions] Length mismatch ({len(personalized_texts)} vs {len(editable_indexes)}), using raw")
    except Exception as e:
        print(f"[personalize-questions] Failed with {error_type(e)}; using raw templates")

    return raw_questions


# Fixed questionnaire ID; attemptId distinguishes repeat intakes per user.
# DEFAULT_FORM_ID moved to app.config (imported above).

def resolve_assessment_appointment_id(user_id: str):
    """The booked assessment this intake belongs to.

    Priority: the appointmentId propagated onto the patient's tagged-questions doc
    (resolved at tag time in clinical-mcp) → else resolve the next BOOKED appointment
    directly from the appointments collection. Returns an ObjectId or None.
    """
    from bson import ObjectId
    from datetime import datetime, timezone

    # 1) Propagated from tag time (clinical-mcp).
    try:
        if tagged_questions_collection is not None:
            td = tagged_questions_collection.find_one(
                {"$or": [{"userId": str(user_id)}, {"userId": normalize_user_id(user_id)}]},
                {"appointmentId": 1},
            )
            if td and td.get("appointmentId"):
                try:
                    return ObjectId(str(td["appointmentId"]))
                except Exception:
                    return td["appointmentId"]
    except Exception as e:
        print(f"[appointment-tag] Tagged-question lookup failed: {error_type(e)}")

    # 2) Fallback: resolve the next booked appointment ourselves.
    try:
        if mongo_client is None:
            return None
        appts = mongo_client[MONGO_DB_NAME]["appointments"]
        pid_variants = []
        try:
            pid_variants.append(ObjectId(str(user_id)))
        except Exception:
            pass
        pid_variants.append(str(user_id))
        now = datetime.now(timezone.utc)
        for pid in pid_variants:
            doc = appts.find_one(
                {"patient": pid, "status": "BOOKED", "appointmentStartTime": {"$gte": now}},
                sort=[("appointmentStartTime", 1)], projection={"_id": 1},
            )
            if doc:
                return doc["_id"]
        for pid in pid_variants:
            doc = appts.find_one(
                {"patient": pid, "status": "BOOKED"},
                sort=[("appointmentStartTime", -1)], projection={"_id": 1},
            )
            if doc:
                return doc["_id"]
    except Exception as e:
        print(f"[appointment-tag] Appointment lookup failed: {error_type(e)}")
    return None


def save_customer_info(
    user_id: str,
    form_data: dict,
    current_section: str,
    form_id: str = None,
    attachments: Optional[List[dict]] = None,
    chat_history: Optional[list] = None,
    preferred_doc_id: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    attempt_id: Optional[str] = None,
    prom_snapshot: Optional[dict] = None,
):
    """
    Save or update customer interview information in MongoDB.
    Forms are unique by ``(userId, formId, attemptId)`` for new records.
    Legacy records without ``attemptId`` remain updateable for compatibility.
    
    Args:
        user_id: The user ID from the selected user (required for uniqueness)
        form_data: The form data dictionary
        current_section: Current section of the interview
        form_id: Optional form ID. If None, uses DEFAULT_FORM_ID.
        lifecycle_status: Explicit lifecycle transition, currently ``completed``
            for terminal interview/PROM events. Ordinary saves infer draft or
            in-progress from the form data.
        attempt_id: Intake-instance identity. When omitted, the legacy
            ``(userId, formId)`` record is selected.
    
    Returns:
        The form_id of the saved form (always DEFAULT_FORM_ID)
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        # Try to reconnect once
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available. Skipping MongoDB save.")
        return None
    
    if not user_id:
        print("Error: user_id is required for saving form data")
        return None
    
    try:
        from datetime import datetime, timezone
        import copy
        
        # Normalize user_id to ObjectId where possible
        normalized_user_id = normalize_user_id(user_id)

        # Use fixed form ID (same for all users)
        if not form_id:
            form_id = DEFAULT_FORM_ID
            print("[save_customer_info] Using default form type")
        else:
            # If form_id is provided, use it (but typically should be DEFAULT_FORM_ID)
            print("[save_customer_info] Using requested form type")
        
        # CRITICAL: Deep copy form_data to prevent shared references
        # This ensures each form has its own independent copy of the data
        form_data_copy = copy.deepcopy(form_data)
        
        # Deterministic title generation avoids an external model request on
        # every save while preserving the existing empty-form lifecycle marker.
        form_title = build_form_title(form_data_copy, empty_title="New Form")
        
        # Prepare the document matching the medical_interview_history.json structure
        save_time = datetime.now(timezone.utc)
        customer_doc = {
            "userId": normalized_user_id,
            "formId": form_id,
            "title": form_title,
            "timestamp": save_time.isoformat(),
            "form_data": form_data_copy,  # Use the deep copy
            "current_section": current_section,
            "updatedAt": save_time,
        }
        if attempt_id is not None:
            normalized_attempt_id = str(attempt_id).strip()
            if not normalized_attempt_id:
                raise ValueError("attempt_id cannot be empty")
            customer_doc["attemptId"] = normalized_attempt_id
        if prom_snapshot is not None:
            customer_doc["promSnapshot"] = copy.deepcopy(prom_snapshot)

        # Tag this intake to the patient's booked assessment so the recommender's
        # customer icon can fetch the right assessment's data. Only set when
        # resolved, so we never clobber an existing tag with null.
        _appt_id = resolve_assessment_appointment_id(user_id)
        if _appt_id is not None:
            customer_doc["appointmentId"] = _appt_id

        # Find existing doc — prefer preferred_doc_id (template doc _id) when provided,
        # then try both ObjectId and plain-string userId so we don't create a second
        # document when external systems stored userId as a string.
        from bson import ObjectId as _ObjId2
        existing = None
        if preferred_doc_id:
            try:
                existing = customer_info_collection.find_one({"_id": _ObjId2(preferred_doc_id)})
            except Exception:
                pass
        if not existing:
            _uid_variants = [normalized_user_id]
            try:
                _uid_variants.append(str(user_id))
            except Exception:
                pass
            for _uid in _uid_variants:
                _existing_query = add_attempt_write_scope(
                    {"formId": form_id, "userId": _uid},
                    attempt_id,
                )
                existing = customer_info_collection.find_one(_existing_query)
                if existing:
                    break

        lifecycle = resolve_form_lifecycle(
            form_data_copy,
            requested_status=lifecycle_status,
            existing_status=existing.get("status") if existing else None,
            existing_completed_at=existing.get("completedAt") if existing else None,
            now=save_time,
        )
        customer_doc["status"] = lifecycle.status
        if lifecycle.expires_at is not None:
            customer_doc["expiresAt"] = lifecycle.expires_at
        if lifecycle.completed_at is not None:
            customer_doc["completedAt"] = lifecycle.completed_at

        attachments_to_store = (
            attachments if attachments is not None else
            (existing.get("attachments", []) if existing else [])
        )
        customer_doc["attachments"] = attachments_to_store

        if existing:
            # Update the doc we found (regardless of how userId was stored)
            update = {"$set": customer_doc}
            if lifecycle.expires_at is None:
                update["$unset"] = {"expiresAt": ""}
            update_filter = build_lifecycle_update_filter(
                existing["_id"],
                requested_status=lifecycle_status,
            )
            customer_info_collection.update_one(update_filter, update)
        else:
            customer_doc["createdAt"] = save_time
            customer_info_collection.insert_one(customer_doc)
        print("[save_customer_info] Customer form persisted")
        
        return form_id
            
    except Exception as e:
        print(f"[save_customer_info] Persistence failed: {error_type(e)}")
        return None


def fetch_user_forms(user_id: str) -> list:
    """
    Retrieve all forms for a specific user from MongoDB.
    
    Args:
        user_id: The user ID to retrieve forms for
        
    Returns:
        List of form documents with formId, title, timestamp, etc.
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return []
    
    try:
        queries = build_user_form_queries(user_id, DEFAULT_FORM_ID)
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        forms = list(
            customer_info_collection.find(
                identity_query,
                {"_id": 0}
            ).sort("createdAt", -1)
        )
        
        formatted_forms = []
        for form in forms:
            formatted_forms.append({
                "formId": form.get("formId", ""),
                "attemptId": form.get("attemptId"),
                "title": form.get("title", "Untitled Form"),
                "timestamp": _serialize_datetime(form.get("timestamp")),
                "createdAt": _serialize_datetime(form.get("createdAt")),
                "updatedAt": _serialize_datetime(form.get("updatedAt")),
                "current_section": form.get("current_section", ""),
                "progress": calculate_form_progress(form.get("form_data", {}))
            })
        
        return formatted_forms
    except Exception as e:
        print(f"[fetch_user_forms] Retrieval failed: {error_type(e)}")
        return []


def fetch_latest_form_for_user(user_id: str) -> dict:
    """
    Retrieve the latest default-form attempt for a specific user.
    """
    global customer_info_collection

    if customer_info_collection is None:
        init_mongo()

    if customer_info_collection is None:
        print("Warning: customer-info collection not available when fetching latest form.")
        return None

    try:
        queries = build_user_form_queries(user_id, DEFAULT_FORM_ID)
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        form = customer_info_collection.find_one(
            identity_query,
            {"_id": 0},
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        if form:
            form["timestamp"] = _serialize_datetime(form.get("timestamp"))
            form["createdAt"] = _serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = _serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"[fetch_latest_form] Retrieval failed: {error_type(e)}")
        return None


def fetch_form_by_id(
    form_id: str,
    user_id: str = None,
    attempt_id: Optional[str] = None,
) -> dict:
    """
    Retrieve an exact attempt, or the latest matching legacy-compatible form.
    
    Args:
        form_id: The form ID to retrieve
        user_id: The user ID (required for uniqueness)
        attempt_id: Optional intake-instance ID. Older clients may omit it and
            receive the latest matching attempt.
        
    Returns:
        Form document or None if not found
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return None
    
    if not user_id:
        print("Warning: user_id is required to fetch form by form_id")
        return None
    
    try:
        # Try ObjectId first, then plain string — external systems may store userId
        # as a string while our backend normalises it to ObjectId.
        from bson import ObjectId as _ObjId
        _uid_variants = [user_id]
        try:
            _uid_variants.insert(0, _ObjId(user_id))
        except Exception:
            pass
        _queries = []
        for _uid in _uid_variants:
            _query = {"formId": form_id, "userId": _uid}
            if attempt_id is not None:
                _normalized_attempt = str(attempt_id).strip()
                if not _normalized_attempt:
                    raise ValueError("attempt_id cannot be empty")
                _query["attemptId"] = _normalized_attempt
            _queries.append(_query)
        _identity_query = _queries[0] if len(_queries) == 1 else {"$or": _queries}
        form = customer_info_collection.find_one(
            _identity_query,
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        if form:
            form["_id"] = str(form.get("_id", ""))
            form["timestamp"] = _serialize_datetime(form.get("timestamp"))
            form["createdAt"] = _serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = _serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"[fetch_form] Retrieval failed: {error_type(e)}")
        return None


def fetch_form_attachments(
    form_id: str,
    user_id: str = None,
    attempt_id: Optional[str] = None,
) -> List[dict]:
    """
    Retrieve attachments for a form.
    The user and optional attempt identity scope the requested form.
    """
    global customer_info_collection

    if customer_info_collection is None:
        init_mongo()

    if customer_info_collection is None:
        return []

    if not user_id:
        print("Warning: user_id is required to fetch form attachments")
        return []

    try:
        queries = build_user_form_queries(
            user_id,
            form_id,
            attempt_id=attempt_id,
        )
        identity_query = queries[0] if len(queries) == 1 else {"$or": queries}
        doc = customer_info_collection.find_one(
            identity_query,
            {"_id": 0, "attachments": 1},
            sort=[("updatedAt", -1), ("createdAt", -1)],
        )
        return doc.get("attachments", []) if doc else []
    except Exception as e:
        print(f"[fetch_attachments] Retrieval failed: {error_type(e)}")
        return []


def create_placeholder_form(
    user_id: str,
    client_state: dict,
    force_new: bool = False,
) -> Optional[str]:
    """Return the form_id for this user WITHOUT creating a MongoDB document.

    We no longer eagerly insert empty 'New Form' documents — that was creating
    one empty doc per connected user, polluting the collection. Instead we just
    assign the well-known DEFAULT_FORM_ID to client_state. The actual MongoDB
    document is created (via upsert) only when the first real data is saved.
    On ordinary connection, the latest attempt is resumed. ``force_new=True``
    assigns a fresh attempt without writing an empty MongoDB document.
    """
    # Check if a form already exists — if so, reuse its exact attempt.
    existing = None if force_new else fetch_latest_form_for_user(user_id)
    if existing:
        existing_id = existing.get("formId", DEFAULT_FORM_ID)
        client_state["form_id"] = existing_id
        client_state["attempt_id"] = resolve_form_attempt_id(existing)
        print("[create_placeholder_form] Reusing existing form")
        return existing_id

    # No existing form, or an explicit new intake: allocate an attempt ID
    # without writing a placeholder document.
    # The document will be created when the first interview answer is saved.
    client_state["form_id"] = DEFAULT_FORM_ID
    client_state["attempt_id"] = resolve_form_attempt_id(
        existing,
        force_new=force_new,
    )
    print("[create_placeholder_form] Assigned default form type; no database write")
    return DEFAULT_FORM_ID



def build_interview_state(client_state: dict, progress_override: Optional[float] = None, use_db_form: bool = False):
    """
    Build interview state for the client.
    
    CRITICAL: Always prefer database form data over health_agent.form to ensure
    each persisted attempt is scoped by user, form, and attempt identity.
    
    Args:
        client_state: Current client state dictionary (must contain user_id and form_id)
        progress_override: Optional progress percentage override
        use_db_form: If True, fetch form data from database for accurate progress calculation
    """
    form_id = client_state.get("form_id")
    user_id = client_state.get("user_id")
    attempt_id = client_state.get("attempt_id")
    attachments = (
        fetch_form_attachments(form_id, user_id, attempt_id)
        if form_id and user_id
        else []
    )
    
    # CRITICAL: Always prefer database form for progress calculation to ensure independence
    # Calculate progress - prefer database form if available (more accurate after uploads)
    form_data_for_progress = None
    if form_id and user_id:
        # Always fetch from database when form_id exists to ensure independence
        form = fetch_form_by_id(form_id, user_id, attempt_id)
        if form and form.get("form_data"):
            form_data_for_progress = form["form_data"]
        else:
            # Fallback to health_agent.form only if form not found in DB
            form_data_for_progress = health_agent.form
    else:
        # No form_id yet, use health_agent.form (will be empty for new forms)
        form_data_for_progress = health_agent.form
    
    _tagged_tmpl = client_state.get("tagged_form_template")
    _prev_prom   = client_state.get("prom_existing_data") or {}
    # It's a PROM session if either the template or previous data is set
    _is_prom     = bool(_tagged_tmpl) or bool(_prev_prom)
    # Full scale list: template keys (remaining) ∪ previous data keys (already answered)
    _all_prom_scales = (
        list({**_prev_prom, **(_tagged_tmpl or {})}.keys()) if _is_prom else []
    )

    # Calculate overall progress
    if progress_override is not None:
        progress = progress_override
    elif _is_prom:
        _prom_fd = form_data_for_progress or {}
        _prom_answered = sum(
            1 for k in _all_prom_scales
            if _prom_scale_is_answered(_prom_fd.get(k)) or _prom_scale_is_answered(_prev_prom.get(k))
        )
        progress = round(_prom_answered / len(_all_prom_scales) * 100) if _all_prom_scales else 0
    else:
        progress = calculate_form_progress(form_data_for_progress)

    # Section completion status
    if _is_prom:
        _prom_fd = form_data_for_progress or {}
        section_progress = [
            {
                "section": scale,
                "is_complete": (
                    _prom_scale_is_answered(_prom_fd.get(scale))
                    or _prom_scale_is_answered(_prev_prom.get(scale))
                ),
                "filled_fields": 1 if (
                    _prom_scale_is_answered(_prom_fd.get(scale))
                    or _prom_scale_is_answered(_prev_prom.get(scale))
                ) else 0,
                "total_fields": 1,
            }
            for scale in _all_prom_scales
        ]
    else:
        section_progress = calculate_section_completion_status(form_data_for_progress)

    # Current section label
    if _is_prom:
        # Remaining scales from template, else fall back to last scale in full list
        _remaining = list(_tagged_tmpl.keys()) if _tagged_tmpl else []
        if _remaining:
            _tidx = max(0, client_state.get("tagged_turn_index", 1) - 1)
            _tidx = min(_tidx, len(_remaining) - 1)
            _section = _remaining[_tidx]
        else:
            _section = _all_prom_scales[-1] if _all_prom_scales else "Outcome Assessment"
    else:
        _section = health_agent.current_section

    return {
        "section": _section,
        "progress": progress,
        "missing_fields": health_agent.missing_fields,
        "attachments": attachments,
        "formId": form_id,
        "attemptId": attempt_id,
        "sectionProgress": section_progress,
        "promSteps": _all_prom_scales if _is_prom else None,
    }


async def build_interview_state_async(
    client_state: dict,
    progress_override: Optional[float] = None,
    use_db_form: bool = False,
):
    """Build legacy interview state without blocking the async event loop."""

    return await run_blocking(
        build_interview_state,
        client_state,
        progress_override,
        use_db_form,
    )


def user_has_reports(user_response: str) -> bool:
    """
    Check if the user's response indicates they have reports.
    
    Args:
        user_response: The user's text response
        
    Returns:
        True if user indicates they have reports, False otherwise
    """
    if not user_response:
        return False
    
    normalized = user_response.lower().strip()
    
    # Special handling: phrases like "i do / i do not" combined with "report"
    if "report" in normalized or "reports" in normalized:
        if ("i do not" in normalized) or ("i don't" in normalized) or ("i dont" in normalized):
            print("[user_has_reports] Detected 'i do not' with reports → treating as NO reports")
            return False
        if "i do" in normalized and not ("i do not" in normalized or "i don't" in normalized or "i dont" in normalized):
            print("[user_has_reports] Detected 'i do' with reports → treating as HAS reports")
            return True
    
    # Positive indicators that user has reports
    positive_indicators = [
        "yes", "yeah", "yep", "i have", "i do have", "i've got", "i got",
        "i have reports", "i have mri", "i have x-ray", "i have x ray",
        "i have ct scan", "i have blood reports", "i have scans",
        "yes i have", "yes i do", "yes i do have", "i do have reports",
        "i have some", "i have them", "yes i have them", "i got reports",
        "i have the reports", "i have my reports", "i brought", "i brought reports"
    ]
    
    # Negative indicators that user doesn't have reports
    negative_indicators = [
        "no", "nope",
        "don't have", "do not have", "don't have any",
        "dont have", "dont have any",
        "no i don't", "no i do not",
        "no i dont", "no i dont have",
        "i don't have", "i do not have",
        "i dont have", "i dont have any",
        "no reports", "no i don't have", "no i dont have reports",
        "none", "nothing", "no i haven't",
        "i haven't", "i have not",
        "no scans", "no mri", "no x-ray"
    ]
    
    # Check for negative first (more definitive)
    for neg in negative_indicators:
        if neg in normalized:
            print(f"[user_has_reports] User indicated NO reports: '{neg}' found in response")
            return False
    
    # Check for positive indicators
    for pos in positive_indicators:
        if pos in normalized:
            print(f"[user_has_reports] User indicated they HAVE reports: '{pos}' found in response")
            return True
    
    # If no clear indicator, check if they mention having specific report types
    report_types = ["mri", "x-ray", "x ray", "ct scan", "blood report", "scan", "reports"]
    has_report_types = any(rt in normalized for rt in report_types)
    
    if has_report_types:
        # If they mention report types without negative words, assume they have them
        print(f"[user_has_reports] User mentioned report types, assuming they have reports")
        return True
    
    return False


def message_requires_attachment(
    current_section: Optional[str], 
    message_text: Optional[str] = None, 
    form_id: Optional[str] = None,
    user_response: Optional[str] = None,
    user_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
) -> bool:
    """
    Show upload card ONLY if:
    1. We're in History & Diagnostics section AND
    2. The user's previous response indicated they HAVE reports (not when asking the question)
    
    Args:
        current_section: Current section name
        message_text: The assistant's message text (for logging)
        form_id: Form ID to check existing attachments
        user_response: The user's response text (to check if they said they have reports)
        user_id: User ID (required to fetch form by formId + userId combination)
    """
    try:
        # If we already have reports attachments for this form, don't ask again
        if form_id and user_id:
            form = fetch_form_by_id(form_id, user_id, attempt_id)
            if form:
                attachments = form.get("attachments", [])
                has_attachments = bool(attachments)
                if has_attachments:
                    print(f"[message_requires_attachment] Attachments already present, not showing upload")
                    return False
    except Exception as e:
        print(f"[message_requires_attachment] Attachment check failed: {error_type(e)}")

    # If the agent is explicitly asking the user to upload reports, always request attachment
    # This is the follow-up prompt like:
    # "Great! Since you mentioned you have reports, would you like to upload them? ..."
    if message_text:
        lower_msg = (message_text if isinstance(message_text, str) else str(message_text)).lower()
        if "would you like to upload" in lower_msg or ("upload them" in lower_msg and "reports" in lower_msg):
            print("[message_requires_attachment] Agent is explicitly asking to upload reports, showing upload card")
            return True

    # CRITICAL: Only show upload if:
    # 1. We're in History & Diagnostics section
    # 2. The agent explicitly asks to upload reports
    #    (user intent about reports is now handled by the LLM inside HealthAgent,
    #     which generates the precise upload prompt text when appropriate).
    if current_section and "History & Diagnostics" in current_section:
        # If this is the initial diagnostic question, do NOT show upload yet
        if message_text:
            question_phrases = [
                "do you have any mri",
                "x-ray",
                "ct scan",
                "blood reports",
                "do you have any reports",
                "any reports related",
            ]
            if any(phrase in message_text.lower() for phrase in question_phrases):
                print("[message_requires_attachment] Agent is asking about reports, not showing upload card yet")
                return False
        else:
            # No user response yet (this is the question being asked), don't show upload
            print(f"[message_requires_attachment] Asking about reports, not showing upload yet (waiting for user response)")
            return False
    
    return False


async def send_text_message(
    websocket: WebSocket,
    client_state: dict,
    text: str,
    interview_state: Optional[dict] = None,
    user_response: Optional[str] = None,
    force_request_attachment: bool = False,
    question_meta: Optional[dict] = None,
):
    """
    Send a text message to the client.

    Args:
        websocket: WebSocket connection
        client_state: Current client state
        text: Message text to send
        interview_state: Optional interview state
        user_response: Optional user's previous response (to check if they have reports)
        force_request_attachment: If True, always show the upload button regardless of heuristics
    """
    if force_request_attachment:
        requires_attachment = True
    else:
        current_section = interview_state.get("section") if interview_state else health_agent.current_section
        requires_attachment = await run_blocking(
            message_requires_attachment,
            current_section,
            text,
            client_state.get("form_id"),
            user_response=user_response,
            user_id=client_state.get("user_id"),
            attempt_id=client_state.get("attempt_id"),
        )

    resolved_interview_state = (
        interview_state
        if interview_state is not None
        else await build_interview_state_async(client_state)
    )

    payload = {
        "type": "text_message",
        "text": text,
        "session_id": client_state["session_id"],
        "interview_state": resolved_interview_state,
        "request_attachment": requires_attachment,
    }
    if question_meta and question_meta.get("type") and question_meta.get("type") != "text":
        payload["question_meta"] = question_meta
    await websocket.send_text(json.dumps(payload))


def reset_health_agent_for_new_interview(client_state: dict, new_user_id: str, agent=None):
    """
    Reset the per-connection HealthAgent for a new interview.
    `agent` is the per-connection HealthAgent instance (no longer global).
    """
    if agent is None:
        return  # safety guard

    previous_user_id = client_state.get("user_id")
    if previous_user_id and previous_user_id != new_user_id:
        print("[reset_health_agent] Subject changed; resetting agent state")
    elif previous_user_id == new_user_id:
        print("[reset_health_agent] Existing subject started a new interview")
    else:
        print("[reset_health_agent] New interview subject; resetting agent state")

    agent.init_form()
    agent.talk_mode = "START"
    agent.history = []
    agent.history.append({"role": "agent", "message": agent.welcome_prompt})

    client_state["user_id"] = new_user_id
    client_state["form_id"] = None
    client_state["attempt_id"] = None
    client_state["prom_snapshot"] = None
    client_state["prom_source_doc_id"] = None

    print(f"[reset_health_agent] HealthAgent reset complete. Form is now empty: {len(agent.form)} sections")


# Initialize database
# MongoDB DISABLED - Commented out
# print("Connecting to MongoDB...")
# db = MedicalInterviewDB()
# print("MongoDB connected")
init_mongo()


@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {
        "status": "ok",
        "service": "healthflex-customer-agent",
        "components": {
            "mongodb": "ok" if customer_info_collection is not None else "degraded",
            "graph": "ok" if _interview_graph is not None else "degraded",
            "stt": "gemini-primary-google-cloud-speech-fallback",
            "authentication": (
                "ok"
                if len(AUTH_SIGNING_SECRET.encode("utf-8")) >= 32
                and AUTH_ISSUER
                and AUTH_AUDIENCE
                else "degraded"
            ),
        },
        "ai_telemetry": {
            "metrics_endpoint": "/metrics" if _HAS_PROMETHEUS else None,
            "pricing_version": PRICING_VERSION,
        },
    }


# def modify_system_prompt():
#     """
#     Modify the system prompt to make the agent more strict about not making assumptions.
#     This modifies the HealthAgent's system prompt directly.
#     """
#     original_prompt = health_agent.system_prompt

#     # Add instructions to avoid making assumptions
#     assumption_instructions = """IMPORTANT: Stick to the question format as much as possible. They are presets."""

#     # Combine with original prompt
#     health_agent.system_prompt = original_prompt + assumption_instructions
#     print("Modified system prompt to reduce assumptions")


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    if _shutting_down:
        await websocket.close(code=1001, reason="Server restarting — reconnect shortly")
        return

    await websocket.accept()
    _active_ws_connections.add(websocket)
    WS_CONNECTIONS.inc()
    _log.info("ws_connected", subject=pseudonymous_id(client_id))
    print("WebSocket client connected")

    # Per-session processing lock — prevents text+audio race condition.
    # Only one message is processed at a time per session.
    _session_lock = asyncio.Lock()

    # ── Per-connection HealthAgent ───────────────────────────────────────────
    # CRITICAL: Each WebSocket connection gets its OWN HealthAgent instance.
    # The global health_agent singleton caused data contamination between
    # concurrent users — one user's form data would overwrite another's.
    # HealthAgent init is lightweight (just LLM + prompts, no ChromaDB).
    health_agent = await run_blocking(HealthAgent)

    # Store client-specific state
    client_state = {
        "session_id": f"session_{int(time.time())}",
        # user_id will be provided by the frontend (real app user ID)
        # via the "start_interview" message. We intentionally avoid
        # generating placeholder IDs like "user_<timestamp>" so that
        # all MongoDB documents are tied to real users from your system.
        "user_id": None,
        "interview_id": None,
        "form_id": None,
        "attempt_id": None,
        "prom_snapshot": None,
        "prom_source_doc_id": None,
        "auth_context": None,
        "first_interaction": True,
        "is_recording": False,
        "received_audio_buffer": bytearray(),
        "recording_start_time": None,
        # Orchestrator session tracking
        "current_question_id": None,
        # LangGraph state (graph_* keys are managed by server_adapter helpers)
        "graph_form": None,
        "graph_question_round": None,
        "graph_current_section": None,
        "graph_phase": None,
        "graph_history": None,
        "graph_form_sections": None,
        "graph_asked_previous_consultations": False,
        "graph_reports_uploaded": False,
        "graph_awaiting_report_upload": False,
        "graph_attempts_on_current_section": 0,
    }
    _session_processing = False  # simple flag to prevent concurrent processing

    # Create a new interview record
    # MongoDB DISABLED - Using placeholder values instead
    # interview_id = db.create_interview(user_id, client_state["session_id"])
    interview_id = f"interview_{int(time.time())}"  # Placeholder interview ID
    client_state["interview_id"] = interview_id
    print(f"WebSocket session started with interview {interview_id} (user will be set on start_interview)")

    # Initialize conversation history
    if not hasattr(health_agent, "history"):
        health_agent.history = []

    # # Make the agent more strict about not making assumptions
    # modify_system_prompt()

    # Don't send welcome message immediately - wait for client to send "start_interview" message
    # This prevents audio from playing before user logs in

    # Main WebSocket loop
    try:
        while True:
            # Receive message from client
            message = await websocket.receive()

            # Client closed the connection. Starlette delivers a single
            # {"type": "websocket.disconnect"} message and raises a RuntimeError
            # ("Cannot call receive once a disconnect message has been received")
            # if receive() is called again. Break cleanly instead of looping back
            # into receive() and crashing the handler.
            if message.get("type") == "websocket.disconnect":
                break

            # Handle text messages (JSON control messages)
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "")

                    if msg_type != "start_interview" and client_state.get("auth_context") is None:
                        await websocket.send_text(
                            json.dumps(
                                {
                                    "type": "error",
                                    "message": "Authentication is required before using this session.",
                                }
                            )
                        )
                        await websocket.close(code=1008, reason="Authentication required")
                        break

                    if msg_type == "start_interview":
                        # Client is ready to start the interview (after login or page reload)
                        from src.prompts import WELCOME_PROMPT, READY_TO_START_PROMPT

                        # Get user_id from the message if provided
                        provided_user_id = data.get("userId")
                        if not provided_user_id:
                            # Frontend must always send a real application user ID.
                            # Without it, we cannot safely tie forms/attachments to users.
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Missing userId in start_interview. Please log in and retry.",
                                    }
                                )
                            )
                            continue

                        try:
                            auth_context = _decode_configured_access_token(
                                data.get("accessToken", "")
                            )
                            authorize_patient(
                                auth_context,
                                provided_user_id,
                                "interview:write",
                            )
                        except AuthConfigurationError:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Authentication is not configured.",
                                    }
                                )
                            )
                            await websocket.close(code=1011, reason="Authentication unavailable")
                            break
                        except (TokenValidationError, AuthorizationError):
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "The consultation link is invalid or has expired.",
                                    }
                                )
                            )
                            await websocket.close(code=1008, reason="Access denied")
                            break

                        client_state["auth_context"] = auth_context

                        # Validate that the user exists in the database
                        if not await run_blocking(validate_user_exists, provided_user_id):
                            # Get database info for error message
                            db_name, collection_name = collection_namespace(
                                users_collection, "users"
                            )
                            error_msg = f"User {provided_user_id} does not exist in {db_name}.{collection_name}. Please use a valid user ID from your user management system."
                            print(f"[start_interview] ERROR: {error_msg}")
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": error_msg,
                                    }
                                )
                            )
                            continue

                        client_state["user_id"] = provided_user_id
                        # formId from URL (e.g. FRM-01, FRM-02); fall back to default
                        provided_form_id = data.get("formId") or DEFAULT_FORM_ID
                        client_state["form_id"] = provided_form_id
                        provided_attempt_id = data.get("attemptId")
                        client_state["attempt_id"] = provided_attempt_id
                        client_state["prom_snapshot"] = None
                        client_state["prom_source_doc_id"] = None
                        # Initialize form_data early so it's always defined regardless of
                        # resume vs new-interview path (avoids UnboundLocalError).
                        form_data = {}

                        # Fetch tagged questions + build PROM form template
                        # Only for non-default forms (FRM-02+); FRM-01 always uses the standard intake flow
                        if provided_form_id == DEFAULT_FORM_ID:
                            _raw_tagged, _prom_template = [], {}
                        else:
                            _raw_tagged, _prom_template, _prom_source_doc_id = await run_blocking(
                                fetch_tagged_questions,
                                provided_user_id,
                                provided_form_id,
                            )
                            # When questions came from an existing customer-info template doc,
                            # store its _id so saves target that doc directly (prevents duplicates).
                            client_state["prom_source_doc_id"] = _prom_source_doc_id

                        _existing_prom_data = {}
                        _existing_prom_snapshot = None
                        if provided_form_id != DEFAULT_FORM_ID:
                            try:
                                _existing_frm02 = await run_blocking(
                                    fetch_form_by_id,
                                    provided_form_id,
                                    provided_user_id,
                                    client_state.get("attempt_id"),
                                )
                                if _existing_frm02:
                                    _existing_prom_data = _existing_frm02.get("form_data", {})
                                    _existing_prom_snapshot = _existing_frm02.get("promSnapshot")
                                if _existing_prom_snapshot:
                                    # Resume the definition actually administered, not a mutable
                                    # question-bank version fetched later in time.
                                    _raw_tagged = questions_from_prom_snapshot(
                                        _existing_prom_snapshot
                                    )
                            except ValueError as _snapshot_error:
                                _log.error(
                                    "prom_snapshot_invalid",
                                    error_type=error_type(_snapshot_error),
                                )
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "message": "This assessment definition cannot be safely resumed. Please contact the clinic.",
                                }))
                                continue
                            except Exception as _prom_load_error:
                                _log.warning(
                                    "prom_resume_lookup_failed",
                                    error_type=error_type(_prom_load_error),
                                )

                        # Initial qid→text lookup (pre-personalization); overwritten below after
                        # personalization so stored keys match what the patient was actually asked.
                        client_state["tagged_question_texts"] = {
                            q["question_id"]: q["text"]
                            for q in _raw_tagged
                            if isinstance(q, dict) and q.get("question_id") and q.get("text")
                        }

                        if _raw_tagged:
                            # Personalize using patient's FRM-01 history
                            try:
                                _frm01 = await run_blocking(
                                    fetch_form_by_id,
                                    DEFAULT_FORM_ID,
                                    provided_user_id,
                                )
                                _patient_ctx = _frm01.get("form_data", {}) if _frm01 else {}
                                _ctx_field_count = sum(
                                    len(v) if isinstance(v, dict) else 1
                                    for v in _patient_ctx.values()
                                ) if _patient_ctx else 0
                                print(f"[personalize-questions] FRM-01 found={_frm01 is not None}, ctx_fields={_ctx_field_count}")
                                _personalized = await run_blocking(
                                    personalize_questions,
                                    _raw_tagged, _patient_ctx, health_agent.llm_complete
                                )
                            except Exception as _pe:
                                print(f"[personalize-questions] Non-fatal: {_pe}")
                                _personalized = _raw_tagged

                            # Rebuild the legacy human-readable form_data view. Stable identity,
                            # exact administered wording and responses are stored separately in
                            # promSnapshot below.
                            _pers_tmpl: dict = {}
                            for _pq in _personalized:
                                if not isinstance(_pq, dict):
                                    continue
                                _pq_id = _pq.get("question_id") or ""
                                _pq_text = _pq.get("text", "").strip()
                                if not _pq_text or not _pq_id:
                                    continue
                                _pq_scale = "Other Assessments"
                                for _pfx3, _pname3 in _PROM_SCALE_MAP.items():
                                    if _pq_id.startswith(_pfx3 + "_"):
                                        _pq_scale = _pname3
                                        break
                                _pers_tmpl.setdefault(_pq_scale, {})[_pq_text] = ""
                            # Preserve original scale order
                            _ordered_tmpl: dict = {}
                            for _os in _prom_template.keys():
                                if _os in _pers_tmpl:
                                    _ordered_tmpl[_os] = _pers_tmpl[_os]
                            for _xs, _xf in _pers_tmpl.items():
                                if _xs not in _ordered_tmpl:
                                    _ordered_tmpl[_xs] = _xf
                            if _ordered_tmpl:
                                _prom_template = _ordered_tmpl
                            # Update qid→text lookup with personalized text
                            client_state["tagged_question_texts"] = {
                                _pq["question_id"]: _pq["text"]
                                for _pq in _personalized
                                if isinstance(_pq, dict) and _pq.get("question_id") and _pq.get("text")
                            }

                            try:
                                client_state["prom_snapshot"] = (
                                    _existing_prom_snapshot
                                    if _existing_prom_snapshot is not None
                                    else build_prom_snapshot(
                                        _personalized,
                                        source_form_id=provided_form_id,
                                        legacy_form_data=_existing_prom_data,
                                    )
                                )
                            except ValueError as _definition_error:
                                _log.error(
                                    "prom_definition_invalid",
                                    error_type=error_type(_definition_error),
                                )
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "message": "This assessment has an invalid question definition. Please contact the clinic.",
                                }))
                                continue

                            # Skip scales already answered in a previous session (do this BEFORE batching
                            # so the positional grouping inside batch_tagged_questions stays correct)
                            if _existing_prom_data and _prom_template:
                                # Group personalized questions by scale via question_id prefix
                                # (same logic as batch_tagged_questions — no positional slicing)
                                _scale_buckets: dict = {}
                                for _pq in _personalized:
                                    _pq_id = (_pq.get("question_id") or "") if isinstance(_pq, dict) else ""
                                    _pq_scale = "Other Assessments"
                                    for _pfx2, _pname2 in _PROM_SCALE_MAP.items():
                                        if _pq_id.startswith(_pfx2 + "_"):
                                            _pq_scale = _pname2
                                            break
                                    _scale_buckets.setdefault(_pq_scale, []).append(_pq)

                                _filtered_personalized = []
                                _filtered_template = {}
                                _skipped_scales = []
                                for _scale_name, _fields in _prom_template.items():
                                    _scale_qs = _scale_buckets.get(_scale_name, [])
                                    _sv = _existing_prom_data.get(_scale_name)
                                    _scale_answered = (
                                        (isinstance(_sv, str) and _sv.strip()) or
                                        (isinstance(_sv, dict) and any(
                                            str(v).strip() for v in _sv.values() if v
                                        ))
                                    )
                                    if _scale_answered:
                                        _skipped_scales.append(_scale_name)
                                    else:
                                        _filtered_personalized.extend(_scale_qs)
                                        _filtered_template[_scale_name] = _fields
                                if _skipped_scales:
                                    print(f"[tagged-questions] Skipping {len(_skipped_scales)} already-answered scales")
                                _personalized = _filtered_personalized
                                _prom_template = _filtered_template

                            try:
                                _batched = await run_blocking(
                                    batch_tagged_questions, _personalized, health_agent.llm_complete, _prom_template
                                )
                            except Exception as _be:
                                print(f"[tagged-questions] batch error (non-fatal): {_be}")
                                _batched = _personalized

                            # Split dicts into parallel text + meta lists
                            _tagged_turns = []
                            _tagged_turn_metas = []
                            for _item in _batched:
                                if isinstance(_item, dict):
                                    _tagged_turns.append(_item.get("text", ""))
                                    _tagged_turn_metas.append({
                                        "type": _item.get("type", "text"),
                                        "options": _item.get("options"),
                                        "question_id": _item.get("question_id"),
                                        "question_ids": _item.get("question_ids"),
                                        "questions": _item.get("questions"),
                                        "question_options": _item.get("question_options"),
                                        "question_types": _item.get("question_types"),
                                        "question_scales": _item.get("question_scales"),
                                    })
                                else:
                                    _tagged_turns.append(_item)
                                    _tagged_turn_metas.append({"type": "text", "options": None, "question_id": None, "question_ids": None, "questions": None})
                            client_state["tagged_turns"] = _tagged_turns
                            client_state["tagged_turn_metas"] = _tagged_turn_metas
                            client_state["tagged_turn_index"] = 0
                            client_state["tagged_form_template"] = _prom_template or None
                            print(f"[tagged-questions] Loaded {len(_tagged_turns)} turns, {len(_prom_template)} PROM scales")

                            # Create skeleton form_data doc immediately so all expected
                            # sections and question fields are visible in the DB from
                            # the first question (answers will be "" until filled in).
                            # Only when there is no existing form_data yet.
                            if _prom_template and not _existing_prom_data:
                                import copy as _cp_skel
                                # _prom_template is already {scale: {field: ""}} — deep
                                # copy it so the skeleton has all sections + question slots.
                                _skeleton = _cp_skel.deepcopy(_prom_template)
                                try:
                                    await run_blocking(
                                        save_customer_info,
                                        provided_user_id, _skeleton, "PROM",
                                        form_id=provided_form_id,
                                        preferred_doc_id=client_state.get("prom_source_doc_id"),
                                        attempt_id=client_state.get("attempt_id"),
                                        prom_snapshot=client_state.get("prom_snapshot"),
                                    )
                                    print(f"[tagged-questions] Created skeleton with {len(_skeleton)} PROM scales")
                                except Exception as _ske:
                                    print(f"[tagged-questions] Skeleton creation failed (non-fatal): {_ske}")
                        else:
                            client_state["tagged_turns"] = None
                            client_state["tagged_turn_metas"] = None
                            client_state["tagged_turn_index"] = 0
                            client_state["tagged_form_template"] = None
                            client_state["prom_snapshot"] = None

                        # Initialize LangGraph state for this connection
                        if _interview_graph is not None:
                            try:
                                init_graph_state_in_client(
                                    client_state,
                                    user_id=provided_user_id,
                                    form_id=provided_form_id,
                                )
                                # Set phase to match whether we're resuming or starting fresh
                                # (will be overwritten when existing form is loaded below)
                                client_state["graph_phase"] = "welcome"
                                print("[graph] Graph state initialized")
                            except Exception as _ge:
                                print(f"[graph] init_graph_state_in_client failed (non-fatal): {_ge}")

                        # Restore existing PROM answers into graph_form so they survive
                        # to the summary step and aren't overwritten with "Not mentioned by patient".
                        # Also persist _existing_prom_data in client_state so the save path can
                        # merge it back in (prevents previously-answered scales being overwritten
                        # with "" when they were skipped in this session).
                        if _existing_prom_data:
                            import copy as _cp
                            _restored_form = _cp.deepcopy(client_state.get("graph_form") or {})
                            for _scale, _val in _existing_prom_data.items():
                                if _prom_scale_is_answered(_val):  # only restore genuinely answered scales
                                    _restored_form[_scale] = _val
                            client_state["graph_form"] = _restored_form
                            _answered_scales = [k for k, v in _existing_prom_data.items() if _prom_scale_is_answered(v)]
                            if _answered_scales:
                                print(f"[start_interview] Restored {len(_answered_scales)} answered PROM scales")
                        # Always store (even if empty) so the save path knows it's a PROM session
                        client_state["prom_existing_data"] = _existing_prom_data

                        # PROM/tagged-questions sessions always start fresh — FRM-02 is a
                        # separate assessment, never a resume of FRM-01.
                        if _raw_tagged:
                            existing_form = None
                            print("[start_interview] PROM mode starting fresh")
                        else:
                            existing_form = (
                                await run_blocking(
                                    fetch_form_by_id,
                                    provided_form_id,
                                    provided_user_id,
                                    provided_attempt_id,
                                )
                                if provided_attempt_id
                                else await run_blocking(
                                    fetch_latest_form_for_user,
                                    provided_user_id,
                                )
                            )

                        if provided_attempt_id and not existing_form:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "The requested form attempt was not found.",
                                    }
                                )
                            )
                            continue

                        # Check if user already has a form (resume mode)
                        if existing_form:
                            print("[start_interview] Resume mode found an existing form")
                            client_state["attempt_id"] = existing_form.get("attemptId")
                            is_resuming = True
                        else:
                            # NEW INTERVIEW MODE: No existing form for this user, start fresh
                            print("[start_interview] New interview mode; resetting agent state")
                            reset_health_agent_for_new_interview(client_state, provided_user_id, agent=health_agent)
                            # reset_health_agent_for_new_interview clears form_id; restore it
                            client_state["form_id"] = provided_form_id
                            is_resuming = False
                        
                        # Handle form initialization based on mode (new vs resume)
                        if is_resuming:
                            # RESUME MODE: Load the existing form from MongoDB
                            print("[start_interview] Loading existing default form")
                            
                            # Load the form from database (using formId + userId combination)
                            existing_form = await run_blocking(
                                fetch_form_by_id,
                                provided_form_id,
                                provided_user_id,
                                client_state.get("attempt_id"),
                            )
                            
                            if existing_form:
                                # Verify this form belongs to the current user (double-check)
                                form_user_id = existing_form.get("userId")
                                # Normalize to strings for comparison (ObjectId vs string)
                                if str(form_user_id) != str(provided_user_id):
                                    print("[start_interview] SECURITY ERROR: form ownership mismatch")
                                    await websocket.send_text(
                                        json.dumps({
                                            "type": "error",
                                            "message": "Access denied: This form belongs to a different user."
                                        })
                                    )
                                    continue
                                
                                # Load the form data — store ONLY in client_state, NEVER in
                                # health_agent.form (global singleton, shared across all users).
                                form_data = existing_form.get("form_data", {})
                                current_section = existing_form.get("current_section", "Present Complaint")

                                # Validate sections using the stateless LangGraph helper
                                # (takes form as a parameter — no global state risk).
                                from src.graph.pure_functions.form_validation import validate_section as _vs
                                pc_complete   = not bool(_vs(form_data, "Present Complaint"))
                                prev_complete = not bool(_vs(form_data, "Previous Consultations"))
                                pain_complete = not bool(_vs(form_data, "Pain Assessment"))
                                hist_complete = not bool(_vs(form_data, "History & Diagnostics"))
                                goals_complete = not bool(_vs(form_data, "Treatment Goals"))
                                referral_complete = not bool(_vs(form_data, "Referral"))

                                def first_incomplete():
                                    if not pc_complete:
                                        return "Present Complaint"
                                    if not prev_complete:
                                        return "Previous Consultations"
                                    if not pain_complete:
                                        return "Pain Assessment"
                                    if not hist_complete:
                                        return "History & Diagnostics"
                                    if not goals_complete:
                                        return "Treatment Goals"
                                    if not referral_complete:
                                        return "Referral"
                                    return current_section

                                # Derive idx and current_section locally from form_data
                                if (not pc_complete) or (not prev_complete):
                                    _resume_idx = 0
                                elif (not pain_complete) or (not hist_complete):
                                    _resume_idx = 1
                                elif (not goals_complete) or (not referral_complete):
                                    _resume_idx = 2
                                else:
                                    _resume_idx = 3

                                _resume_section = first_incomplete()

                                # Set health_agent ONLY to enable talk_to_user/generate_summary below.
                                # These assignments happen atomically just before use — minimising
                                # the window where another concurrent user can overwrite them.
                                # health_agent is still a global, but this is the only place we write it.
                                health_agent.form = form_data
                                health_agent.current_section = _resume_section
                                health_agent.idx = _resume_idx
                                health_agent.talk_mode = "USER"

                                # Always resume into interviewing phase so the graph never
                                # re-runs the welcome/first-turn logic on reconnect.
                                if client_state.get("graph_phase") is not None:
                                    client_state["graph_phase"] = "interviewing"

                                # CRITICAL: Sync the full graph state from MongoDB so the first
                                # user turn after a redeploy doesn't overwrite saved data with
                                # an empty template. build_graph_state falls back to fresh defaults
                                # for any key that is None — without these lines every redeploy
                                # wipes the patient's data on their first message.
                                client_state["graph_form"] = form_data
                                client_state["graph_current_section"] = health_agent.current_section

                                # Derive the correct question_round from section completion so the
                                # graph asks questions at the right depth (not restarting from round 0).
                                _idx = health_agent.idx  # 0/1/2/3 set just above
                                client_state["graph_question_round"] = _idx if _idx < 3 else 2

                                # Derive the ordered form_sections list
                                from src.prompts import get_medical_form_template as _get_tmpl
                                client_state["graph_form_sections"] = list(_get_tmpl().keys())


                                print(f"[start_interview] Loaded form at section index {health_agent.idx}")
                                
                                # Decide whether we have enough information to show a full summary
                                filled_field_count = 0
                                for section, fields in form_data.items():
                                    if isinstance(fields, dict):
                                        for _, value in fields.items():
                                            if value and str(value).strip():
                                                filled_field_count += 1

                                summary_text = None
                                MIN_FIELDS_FOR_SUMMARY = 5  # require a bit of data before showing a full summary

                                if filled_field_count >= MIN_FIELDS_FOR_SUMMARY:
                                    # Generate a summary of information collected so far
                                    try:
                                        summary_text = await run_blocking(
                                            health_agent.generate_summary
                                        )
                                    except Exception as e:
                                        print(f"[start_interview] Resume summary failed: {error_type(e)}")
                                        summary_text = None


                                # Build resume message + next question to ask immediately.
                                from src.prompts import PREDEFINED_QUESTIONS
                                if summary_text:
                                    resume_message = (
                                        "Welcome back! Here's a quick summary of what I've noted so far:\n\n"
                                        f"{summary_text}\n\n"
                                        "Let's continue from where we left off."
                                    )
                                else:
                                    resume_message = (
                                        "Welcome back. We haven't collected much information yet — "
                                        "let's continue right away."
                                    )

                                await send_text_message(
                                    websocket,
                                    client_state,
                                    resume_message,
                                    await build_interview_state_async(client_state),
                                    user_response=None
                                )

                                # Send the next question immediately so the user sees what to answer.
                                # If tagged turns exist, use the first turn; otherwise use standard flow.
                                from src.prompts import PREDEFINED_QUESTIONS
                                _tagged_turns = client_state.get("tagged_turns")
                                if _tagged_turns:
                                    next_q_text = _tagged_turns[0]
                                    client_state["tagged_turn_index"] = 1
                                elif filled_field_count < 3:
                                    # Too little data — ask the comprehensive opening question
                                    next_q_text = PREDEFINED_QUESTIONS[0][1]
                                else:
                                    try:
                                        prompt_template = health_agent.make_template(mode="query")
                                        next_q_text = await run_blocking(
                                            health_agent.talk_to_user,
                                            prompt_template,
                                        )
                                    except Exception as _qe:
                                        print(f"[start_interview] Could not generate next question: {_qe}")
                                        next_q_text = PREDEFINED_QUESTIONS[0][1]

                                await send_text_message(
                                    websocket,
                                    client_state,
                                    next_q_text,
                                    await build_interview_state_async(client_state),
                                    user_response=None
                                )

                                continue
                            else:
                                print("[start_interview] Requested form not found; starting a new interview")
                                # Fall through to create a new form
                                is_resuming = False
                                reset_health_agent_for_new_interview(client_state, provided_user_id, agent=health_agent)
                        
                        # NEW INTERVIEW MODE: Verify reset and create new form
                        if not is_resuming:
                            # CRITICAL: Verify reset was successful - form should be empty
                            form_is_empty = True
                            for section, fields in form_data.items():
                                if isinstance(fields, dict):
                                    for field, value in fields.items():
                                        if value and str(value).strip():
                                            form_is_empty = False
                                            print(f"[start_interview] Reset verification failed at {section}.{field}")
                            
                            if form_is_empty:
                                print("[start_interview] Interview started with empty form state")
                            else:
                                print(f"[start_interview] ✗ WARNING: Form has data after reset! Forcing another reset...")
                                health_agent.init_form()
                                health_agent.talk_mode = "START"
                                health_agent.history = []
                                health_agent.history.append({"role": "agent", "message": health_agent.welcome_prompt})

                            # Create a new placeholder form (skipped for PROM — form_id already set)
                            if not _raw_tagged:
                                print("[start_interview] Creating placeholder form")
                                form_id = await run_blocking(
                                    create_placeholder_form,
                                    client_state["user_id"],
                                    client_state,
                                )
                            else:
                                form_id = provided_form_id
                                print("[start_interview] PROM mode uses requested form; no placeholder")
                            # Verify the form was created with empty data
                            if form_id:
                                form_check = await run_blocking(
                                    fetch_form_by_id,
                                    form_id,
                                    client_state["user_id"],
                                    client_state.get("attempt_id"),
                                )
                                if form_check:
                                    form_data_check = form_check.get("form_data", {})
                                    # Log what was actually saved
                                    print("[start_interview] Placeholder created; verifying empty state")
                                    # Check if any fields have non-empty values
                                    has_data = False
                                    for section, fields in form_data_check.items():
                                        if isinstance(fields, dict):
                                            for field, value in fields.items():
                                                if value and str(value).strip():
                                                    has_data = True
                                                    print(f"[start_interview] WARNING: non-empty value found at {section}.{field}")
                                    if not has_data:
                                        print("[start_interview] Placeholder verified empty")
                                    else:
                                        print("[start_interview] ERROR: placeholder contains existing data")

                            # CRITICAL: Ensure health_agent.form is still empty before proceeding
                            # Check if health_agent.form has been contaminated after placeholder creation
                            form_has_data = False
                            for section, fields in form_data.items():
                                if isinstance(fields, dict):
                                    for field, value in fields.items():
                                        if value and str(value).strip():
                                            form_has_data = True
                            print(f"[start_interview] WARNING: agent state contains data at {section}.{field}")
                            
                            if form_has_data:
                                print(f"[start_interview] CRITICAL: health_agent.form was contaminated! Resetting again...")
                                health_agent.init_form()

                        # For new interviews: send a warm welcome, then immediately
                        # follow with the comprehensive first question.
                        from src.prompts import PREDEFINED_QUESTIONS, WELCOME_PROMPT
                        _tagged_turns = client_state.get("tagged_turns")
                        _tagged_turn_metas = client_state.get("tagged_turn_metas") or []
                        # All PROM scales already answered → show completion, don't fall through to FRM-01
                        _all_prom_done = bool(_raw_tagged) and (_tagged_turns is not None) and len(_tagged_turns) == 0
                        if _all_prom_done:
                            await send_text_message(
                                websocket,
                                client_state,
                                "Welcome back! It looks like you've already completed all your outcome assessments. "
                                "Your responses have been recorded — thank you!",
                                await build_interview_state_async(client_state, progress_override=100.0, use_db_form=True),
                                user_response=None,
                            )
                            continue
                        if _tagged_turns:
                            # Use first tagged turn; graph will use index 1, 2, ... on subsequent turns
                            first_question = _tagged_turns[0]
                            _first_q_meta = _tagged_turn_metas[0] if _tagged_turn_metas else None
                            client_state["tagged_turn_index"] = 1
                        elif provided_form_id != DEFAULT_FORM_ID:
                            # Non-FRM-01 form with no PROM questions left → already handled by
                            # _all_prom_done guard above. This branch is a safety net: never
                            # serve FRM-01 intake questions on an FRM-02+ URL.
                            await send_text_message(
                                websocket,
                                client_state,
                                "It looks like there are no outcome assessment questions assigned to this form yet. "
                                "Please check with your clinician.",
                                await build_interview_state_async(client_state, progress_override=100.0, use_db_form=True),
                                user_response=None,
                            )
                            continue
                        else:
                            first_question = PREDEFINED_QUESTIONS[0][1]
                            _first_q_meta = None
                        if client_state.get("graph_phase") is not None:
                            client_state["graph_phase"] = "interviewing"

                        # Use empty form for progress on new interview (health_agent.form is global/shared)
                        progress = 0.0

                        # Welcome message — shorter for PROM sessions (patient already went through FRM-01)
                        if _tagged_turns:
                            _welcome_text = (
                                "Welcome back! I have a few standardised outcome questions to understand "
                                "how your condition has been affecting your daily life. "
                                "Please answer as honestly as you can — there are no right or wrong answers."
                            )
                        else:
                            _welcome_text = WELCOME_PROMPT.strip()
                        await send_text_message(
                            websocket,
                            client_state,
                            _welcome_text,
                            await build_interview_state_async(
                                client_state, progress_override=progress, use_db_form=True
                            ),
                            user_response=None
                        )

                        # First question immediately after
                        await send_text_message(
                            websocket,
                            client_state,
                            first_question,
                            await build_interview_state_async(
                                client_state, progress_override=progress, use_db_form=True
                            ),
                            user_response=None,
                            question_meta=_first_q_meta,
                        )

                    elif msg_type == "start_new_form":
                        # User wants to start a new form
                        from src.prompts import WELCOME_PROMPT, READY_TO_START_PROMPT
                        
                        # Ensure we have a valid user_id (may be provided in this message)
                        provided_user_id = data.get("userId")
                        target_user_id = client_state.get("user_id") or provided_user_id

                        if target_user_id is None:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Cannot start new form without a userId. Please log in again.",
                                    }
                                )
                            )
                            continue

                        try:
                            authorize_patient(
                                client_state["auth_context"],
                                target_user_id,
                                "interview:write",
                            )
                        except AuthorizationError:
                            await websocket.send_text(
                                json.dumps({"type": "error", "message": "Access denied."})
                            )
                            continue

                        if client_state.get("user_id") is None:
                            client_state["user_id"] = target_user_id
                            print("[start_new_form] Associated authorized subject with session")

                        # Clear the old form_id from client_state to ensure a fresh start
                        client_state["form_id"] = None
                        client_state["attempt_id"] = None
                        
                        # Reset the health agent for a new form (this clears form data)
                        reset_health_agent_for_new_interview(client_state, client_state["user_id"], agent=health_agent)
                        health_agent.talk_mode = "START"
                        
                        # Create a new placeholder form with fresh form_id
                        new_form_id = await run_blocking(
                            create_placeholder_form,
                            client_state["user_id"],
                            client_state,
                            True,
                        )
                        client_state["tagged_turns"] = None
                        client_state["tagged_turn_metas"] = None
                        client_state["tagged_turn_index"] = 0
                        client_state["tagged_form_template"] = None
                        client_state["prom_existing_data"] = {}
                        client_state["prom_snapshot"] = None
                        client_state["prom_source_doc_id"] = None
                        if _interview_graph is not None and new_form_id:
                            init_graph_state_in_client(
                                client_state,
                                user_id=client_state["user_id"],
                                form_id=new_form_id,
                            )
                            client_state["graph_phase"] = "welcome"
                        
                        print("[start_new_form] Form state reset requested")
                        print(f"[start_new_form] health_agent.form after reset - keys: {list(health_agent.form.keys())}")
                        
                        # Verify the new form is empty
                        if new_form_id:
                            form_check = await run_blocking(
                                fetch_form_by_id,
                                new_form_id,
                                client_state["user_id"],
                                client_state.get("attempt_id"),
                            )
                            if form_check:
                                form_data_check = form_check.get("form_data", {})
                                has_data = any(
                                    v and str(v).strip() if not isinstance(v, dict) 
                                    else any(val and str(val).strip() for val in v.values())
                                    for v in form_data_check.values()
                                )
                                if has_data:
                                    print("[start_new_form] ERROR: new form state contains data")
                                else:
                                    print("[start_new_form] New form state verified empty")
                        
                        welcome_text = WELCOME_PROMPT.strip()
                        welcome_text += "\n\n" + READY_TO_START_PROMPT.strip()
                        
                        # Use database form for progress (should be 0% for new empty form)
                        if new_form_id:
                            form_from_db = await run_blocking(
                                fetch_form_by_id,
                                new_form_id,
                                client_state["user_id"],
                                client_state.get("attempt_id"),
                            )
                            if form_from_db and form_from_db.get("form_data"):
                                progress = calculate_form_progress(form_from_db["form_data"])
                            else:
                                progress = calculate_form_progress(health_agent.form)
                        else:
                            progress = calculate_form_progress(health_agent.form)
                        
                        await send_text_message(
                            websocket,
                            client_state,
                            welcome_text,
                            await build_interview_state_async(
                                client_state, progress_override=progress
                            ),
                            user_response=None
                        )

                    elif msg_type == "load_form":
                        # User wants to load an existing form
                        form_id = data.get("formId", "")
                        attempt_id = data.get("attemptId")
                        if not form_id:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form ID is required",
                                    }
                                )
                            )
                            continue
                        
                        # CRITICAL: Check if start_interview just created an empty form
                        # If so, we should delete it before loading the existing form to prevent orphaned forms
                        existing_form_id = client_state.get("form_id")
                        user_id_for_check = client_state.get("user_id")
                        if existing_form_id and existing_form_id != form_id and user_id_for_check:
                            # Check if the existing form is empty (just created by start_interview)
                            existing_form = await run_blocking(
                                fetch_form_by_id,
                                existing_form_id,
                                user_id_for_check,
                                client_state.get("attempt_id"),
                            )
                            if existing_form:
                                form_data_existing = existing_form.get("form_data", {})
                                is_empty = True
                                for section, fields in form_data_existing.items():
                                    if isinstance(fields, dict):
                                        for field, value in fields.items():
                                            if value and str(value).strip():
                                                is_empty = False
                                                break
                                        if not is_empty:
                                            break
                                
                                if is_empty:
                                    print("[load_form] Removing empty placeholder before loading requested form")
                                    # Delete the empty form to prevent orphaned forms in the database
                                    try:
                                        if customer_info_collection is not None:
                                            delete_filter = build_owned_form_filter(
                                                existing_form,
                                                user_id_for_check,
                                                existing_form_id,
                                            )
                                            result = await run_blocking(
                                                customer_info_collection.delete_one,
                                                delete_filter,
                                            )
                                            if result.deleted_count > 0:
                                                print("[load_form] Deleted empty placeholder")
                                            else:
                                                print("[load_form] Empty placeholder was already absent")
                                        else:
                                            print("[load_form] MongoDB unavailable; cannot delete empty placeholder")
                                    except Exception as e:
                                        print(f"[load_form] Placeholder deletion failed: {error_type(e)}")
                                    # Clear the form_id from client_state so we use the new one
                                    client_state.pop("form_id", None)
                        
                        # Retrieve the form from database
                        user_id_for_load = client_state.get("user_id")
                        if not user_id_for_load:
                            await websocket.send_text(
                                json.dumps({
                                    "type": "error",
                                    "text": "User ID is required to load form",
                                })
                            )
                            continue
                        form = await run_blocking(
                            fetch_form_by_id,
                            form_id,
                            user_id_for_load,
                            attempt_id,
                        )
                        if not form:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form not found",
                                    }
                                )
                            )
                            continue
                        
                        # CRITICAL: If user_id is not set yet, get it from the form
                        # This handles the case where load_form is called before start_interview
                        form_user_id = form.get("userId")
                        current_user_id = client_state.get("user_id")
                        
                        # If client_state doesn't have user_id, set it from the form
                        # (This handles load_form being called before start_interview)
                        if not current_user_id and form_user_id:
                            print("[load_form] Associated stored subject with session")
                            client_state["user_id"] = form_user_id
                            current_user_id = form_user_id
                        
                        # Verify form belongs to current user (security check)
                        if form_user_id and current_user_id and form_user_id != current_user_id:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form does not belong to current user",
                                    }
                                )
                            )
                            print("[load_form] Security check failed: form ownership mismatch")
                            continue
                        
                        # CRITICAL: Initialize/reset health_agent BEFORE loading form data to prevent data leakage
                        # This ensures no previous user's data contaminates this form load
                        # Also initialize user_id if not set
                        if not client_state.get("user_id"):
                            reset_health_agent_for_new_interview(client_state, form_user_id, agent=health_agent)
                        else:
                            health_agent.init_form()
                            health_agent.talk_mode = "START"
                            health_agent.history = []
                            health_agent.history.append({"role": "agent", "message": health_agent.welcome_prompt})
                        
                        # Load form data from database and DEEP COPY it to prevent shared references
                        import copy
                        form_data = form.get("form_data", {})
                        form_data_copy = copy.deepcopy(form_data)  # CRITICAL: Deep copy to prevent sharing
                        current_section = form.get("current_section", "")
                        
                        # Update health agent with the deep copied form data
                        health_agent.form = form_data_copy
                        health_agent.current_section = current_section
                        
                        # CRITICAL: Set talk_mode to USER if form has data (not a fresh form)
                        # This prevents the system from asking the initial question again
                        has_form_data = any(
                            isinstance(fields, dict) and any(str(v).strip() for v in fields.values())
                            for fields in form_data_copy.values()
                        )
                        if has_form_data:
                            health_agent.talk_mode = "USER"
                            print("[load_form] Existing data loaded; switching to user mode")
                        
                        # Determine the correct idx AND set current_section to first incomplete section
                        # idx=0: Present Complaint and Pain Assessment
                        # idx=1: Previous Consultations and History & Diagnostics
                        # idx=2: Treatment Goals and Referral
                        present_complaint_complete = health_agent.validator("Present Complaint")
                        pain_assessment_complete = health_agent.validator("Pain Assessment")
                        prev_consultations_complete = health_agent.validator("Previous Consultations")
                        history_diag_complete = health_agent.validator("History & Diagnostics")
                        treatment_goals_complete = health_agent.validator("Treatment Goals")
                        referral_complete = health_agent.validator("Referral")

                        # Helper to set current_section to the first incomplete section in order
                        def set_first_incomplete_section():
                            if not present_complaint_complete:
                                return "Present Complaint"
                            if not pain_assessment_complete:
                                return "Pain Assessment"
                            if not prev_consultations_complete:
                                return "Previous Consultations"
                            if not history_diag_complete:
                                return "History & Diagnostics"
                            if not treatment_goals_complete:
                                return "Treatment Goals"
                            if not referral_complete:
                                return "Referral"
                            return health_agent.current_section  # all complete, keep as-is

                        # Determine idx based on completion status
                        if present_complaint_complete and pain_assessment_complete:
                            if prev_consultations_complete and history_diag_complete:
                                if treatment_goals_complete and referral_complete:
                                    # All sections complete - interview is done
                                    health_agent.idx = 3  # Beyond last predefined question
                                else:
                                    # On idx=2 (Treatment Goals and Referral)
                                    health_agent.idx = 2
                            else:
                                # On idx=1 (Previous Consultations and History & Diagnostics)
                                health_agent.idx = 1
                        else:
                            # On idx=0 (Present Complaint and Pain Assessment)
                            health_agent.idx = 0

                        # Set current_section to the first incomplete section so progress matches questions
                        health_agent.current_section = set_first_incomplete_section()
                        print(f"[load_form] Set current_section to first incomplete section: {health_agent.current_section}, idx: {health_agent.idx}")
                        
                        print(f"[load_form] Restored idx={health_agent.idx} based on section completion status")
                        
                        print(f"[load_form] Loaded deep-copied form state at index {health_agent.idx}")
                        
                        # Store form_id in client state for saving
                        client_state["form_id"] = form_id
                        client_state["attempt_id"] = form.get("attemptId")
                        
                        # Calculate progress using the deep copied data
                        progress = calculate_form_progress(form_data_copy)
                        
                        # Generate the next question or summary that should be shown
                        next_message = None
                        is_complete = (present_complaint_complete and pain_assessment_complete and 
                                      prev_consultations_complete and history_diag_complete and 
                                      treatment_goals_complete and referral_complete)
                        
                        if has_form_data:
                            if is_complete:
                                # Form is complete - show summary for confirmation
                                try:
                                    summary = await run_blocking(
                                        health_agent.generate_summary
                                    )
                                    health_agent.conversation_state['awaiting_summary_confirmation'] = True
                                    health_agent.history.append({"role": "agent", "message": summary})
                                    next_message = summary
                                    print(f"[load_form] Form is complete, generated summary for confirmation")
                                except Exception as e:
                                    print(f"[load_form] Summary generation failed: {error_type(e)}")
                            else:
                                # Form has data but is not complete - generate next question
                                try:
                                    # Try to generate question for current section
                                    prompt_template = health_agent.make_template(mode="query")
                                    if prompt_template == "SKIP_SECTION":
                                        # Current section should be skipped, move to next section
                                        print(f"[load_form] Current section {health_agent.current_section} should be skipped, moving to next")
                                        # Move to next section
                                        current_index = health_agent.form_sections.index(health_agent.current_section)
                                        if current_index < len(health_agent.form_sections) - 1:
                                            health_agent.current_section = health_agent.form_sections[current_index + 1]
                                            # Try generating question for new section
                                            prompt_template = health_agent.make_template(mode="query")
                                    
                                    if prompt_template and prompt_template != "SKIP_SECTION":
                                        next_message = await run_blocking(
                                            health_agent.talk_to_user,
                                            prompt_template,
                                        )
                                        # Add to history
                                        health_agent.history.append({"role": "agent", "message": next_message})
                                        print(f"[load_form] Generated next question ({len(next_message)} chars)")
                                    else:
                                        print(f"[load_form] Could not generate question (section may be complete or skipped)")
                                except Exception as e:
                                    print(f"[load_form] Question generation failed: {error_type(e)}")
                        
                        # Send confirmation and current state (use deep copy, not original)
                        response_data = {
                            "type": "form_loaded",
                            "text": f"Loaded form: {form.get('title', 'Untitled Form')}",
                            "session_id": client_state["session_id"],
                            "interview_state": await build_interview_state_async(
                                client_state, progress_override=progress
                            ),
                            "form_data": form_data_copy,  # Use deep copy
                        }
                        
                        await websocket.send_text(json.dumps(response_data))
                        
                        # Send the question/summary as the primary message (replaces "Loaded form" in UI)
                        if next_message:
                            # Send the question/summary as a text message (same format as normal interview flow)
                            await send_text_message(
                                websocket,
                                client_state,
                                next_message,
                                interview_state=await build_interview_state_async(client_state, progress_override=progress),
                                user_response=None
                            )
                            print(f"[load_form] Sent next question/summary ({len(next_message)} chars)")
                        else:
                            # If no question could be generated, at least send a message indicating we're ready
                            await send_text_message(
                                websocket,
                                client_state,
                                "Welcome back! Let's continue with your medical interview.",
                                interview_state=await build_interview_state_async(client_state, progress_override=progress),
                                user_response=None
                            )
                        
                        # Continue from where they left off

                    elif msg_type == "end_session":
                        provided_user_id = data.get("userId")
                        if provided_user_id:
                            try:
                                authorize_patient(
                                    client_state["auth_context"],
                                    provided_user_id,
                                    "interview:write",
                                )
                            except AuthorizationError:
                                await websocket.send_text(
                                    json.dumps({"type": "error", "message": "Access denied."})
                                )
                                continue
                            client_state["user_id"] = provided_user_id

                        try:
                            import copy
                            health_agent.save_progress()
                            form_id = client_state.get("form_id")
                            await run_blocking(
                                save_customer_info,
                                user_id=client_state["user_id"],
                                form_data=copy.deepcopy(health_agent.form),  # Deep copy to prevent sharing
                                current_section=health_agent.current_section,
                                form_id=form_id,
                                attempt_id=client_state.get("attempt_id"),
                            )
                            await send_text_message(
                                websocket,
                                client_state,
                                "Interview summary saved.",
                                await build_interview_state_async(
                                    client_state,
                                    progress_override=calculate_form_progress(
                                        health_agent.form
                                    ),
                                ),
                                user_response=None
                            )
                        except Exception as e:
                            print(f"[end_session] Save failed: {error_type(e)}")

                    elif msg_type == "audio_start":
                        # Client is starting to send audio
                        client_state["is_recording"] = True
                        client_state["received_audio_buffer"] = bytearray()
                        # Use a monotonic server clock; client timestamps can be
                        # absent, use different units, or be manipulated.
                        client_state["recording_start_time"] = time.monotonic()
                        print("Client started sending audio")

                    elif msg_type == "text_input":
                        # Client is sending text input (manual or auto-sent transcription)
                        text_input = data.get("text", "").strip()
                        if not text_input:
                            continue

                        try:
                            _request_decision = _request_window.register(
                                client_state.get("user_id"),
                                data.get("requestId"),
                                data.get("questionId"),
                            )
                        except ValueError as _request_error:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "text": str(_request_error),
                            }))
                            continue
                        if _request_decision is RequestDecision.CONFLICT:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "text": "requestId was already used for a different question",
                            }))
                            continue
                        if _request_decision is RequestDecision.DUPLICATE:
                            await websocket.send_text(json.dumps({
                                "type": "submission_ack",
                                "status": "duplicate",
                                "requestId": data.get("requestId"),
                            }))
                            continue

                        text_input = sanitize_patient_input(text_input)
                        if not _rate_limiter.check(client_state.get("user_id", "anon")):
                            await websocket.send_text(json.dumps({"type": "error", "text": "Too many messages. Please slow down."}))
                            continue

                        print(f"[text_input] Received patient input ({len(text_input)} chars)")
                        print(f"[text_input] Current talk_mode: {health_agent.talk_mode}, history length: {len(health_agent.history)}")

                        # Identify only fully validated multi-answer UI payloads.
                        # Voice/free-form input remains on the existing AI path.
                        _prom_meta_for_input = None
                        _prom_meta_index = client_state.get("tagged_turn_index", 1) - 1
                        _prom_metas_for_input = client_state.get("tagged_turn_metas") or []
                        if 0 <= _prom_meta_index < len(_prom_metas_for_input):
                            _prom_meta_for_input = _prom_metas_for_input[_prom_meta_index]
                        _structured_prom_answers = parse_structured_prom_answers(
                            text_input,
                            _prom_meta_for_input,
                            data.get("inputMode"),
                        )

                        # Deterministic clinical safety boundary. This must remain before
                        # every off-topic, assessment, extraction, and generation LLM call.
                        _escalation = assess_urgent_risk(
                            text_input,
                            question_meta=_prom_meta_for_input,
                            structured_answers=_structured_prom_answers,
                        )
                        if _escalation is not None:
                            if clinical_escalations_collection is not None:
                                try:
                                    await run_blocking(
                                        record_escalation,
                                        clinical_escalations_collection,
                                        _escalation,
                                        patient_id=client_state.get("user_id") or "",
                                        form_id=client_state.get("form_id"),
                                        attempt_id=client_state.get("attempt_id"),
                                        session_id=client_state.get("session_id"),
                                        request_id=data.get("requestId"),
                                    )
                                except Exception as _escalation_store_error:
                                    # Detection is fail-safe: storage failure must never resume AI.
                                    _log.error(
                                        "clinical_escalation_persist_failed",
                                        error_type=error_type(_escalation_store_error),
                                    )
                            _log.warning(
                                "clinical_escalation_detected",
                                subject=pseudonymous_id(client_state.get("user_id")),
                                rule_id=_escalation.rule_id,
                                category=_escalation.category,
                                severity=_escalation.severity,
                                policy_version=_escalation.policy_version,
                            )
                            await websocket.send_text(json.dumps({
                                "type": "clinical_escalation",
                                "text": _escalation.patient_message,
                                "category": _escalation.category,
                                "severity": _escalation.severity,
                                "policyVersion": _escalation.policy_version,
                                "stopInterview": _escalation.stop_interview,
                            }))
                            await websocket.close(code=4003, reason="Clinical escalation")
                            break

                        # ── Off-topic question shortcut — answer WITHOUT touching the graph ──
                        # Detects two categories:
                        # 1. Brand questions → query ChromaDB (Stance brandbook)
                        # 2. General medical/educational questions → answer from LLM knowledge
                        # In both cases the interview state is fully preserved and Sage
                        # guides the patient back to the form after answering.
                        #
                        # Guard: skip for long messages (>25 words) or on the first turn.
                        # Long messages are detailed medical responses, not off-topic questions.
                        # First turn (talk_mode START) is always the intake response.
                        _ti_lower = text_input.lower()
                        _word_count = len(text_input.split())
                        _off_topic_eligible = (
                            _structured_prom_answers is None
                            and health_agent.talk_mode != "START"
                            and _word_count <= 25
                        )

                        # "stance" alone always means Stance Health — catch it too
                        _is_just_stance = (
                            _ti_lower.strip() in {"what is stance", "what is stance?",
                                                   "stance?", "stance health", "stance health?"}
                            or _ti_lower.startswith("what is stance")
                            or _ti_lower.startswith("tell me about stance")
                            or "stance health" in _ti_lower
                            or ("stance" in _ti_lower and len(_ti_lower.split()) <= 6
                                and "knee" not in _ti_lower and "pain" not in _ti_lower)
                        )
                        _brand_signals = [
                            "about stance", "what is stance", "tell me about stance",
                            "stance health", "your clinic", "the clinic", "your services",
                            "physiotherapy service", "how does stance", "about you",
                            "who are you", "what do you do", "what does stance",
                            "stance team", "stance location", "where are you located",
                            "how many center", "how many clinic", "how many branch",
                            "appointment", "book a session", "treatment options",
                            "what do you offer", "tell me more about stance",
                        ]
                        _edu_signals = [
                            "tell me more about", "can u tell me", "can you tell me",
                            "what are the causes", "what causes", "what is the reason",
                            "what are the symptoms", "how does", "explain", "what is",
                            "what are", "how do i", "what should i", "what can",
                            "tell me about", "more about", "info on", "information on",
                            "difference between", "what happens", "why does",
                        ]

                        # Assessment requests — patient asking Sage for a clinical opinion
                        _assessment_signals = [
                            "what do you think", "what do u think", "your opinion",
                            "what would you say", "what's the situation", "what is the situation",
                            "what could it be", "what might it be", "likely diagnosis",
                            "preliminary assessment", "what do you reckon", "any idea",
                            "what's wrong", "what is wrong", "your assessment",
                            "sounds like", "does it sound like", "is it serious",
                            "should i be worried", "how bad is it", "what should i do",
                        ]
                        _is_assessment = _off_topic_eligible and any(s in _ti_lower for s in _assessment_signals)
                        _is_brand = _off_topic_eligible and (_is_just_stance or any(s in _ti_lower for s in _brand_signals))
                        _is_edu = _off_topic_eligible and any(s in _ti_lower for s in _edu_signals)

                        if _is_assessment:
                            try:
                                # Build a summary of what's been collected so far
                                _form = client_state.get("graph_form") or {}
                                _collected = []
                                for _sec, _fields in _form.items():
                                    if isinstance(_fields, dict):
                                        for _k, _v in _fields.items():
                                            if _v and str(_v).strip():
                                                _collected.append(f"{_k}: {_v}")

                                _summary = "\n".join(_collected) if _collected else "No information collected yet."
                                _assess_prompt = f"""You are Sage, a warm and knowledgeable physiotherapy assistant at Stance Health.

Based on what the patient has shared so far, give a brief, empathetic preliminary impression (3-5 sentences).
Be honest but reassuring. Use simple language. Make clear this is preliminary — the clinical assessment
will be done properly by the physiotherapist.

Do NOT suggest a definitive diagnosis. Do say something like "Based on what you've described..."
and end with "Your physiotherapist will do a full assessment to confirm this."

Information collected:
{_summary}

Patient just asked: "{text_input}"

Your response:"""
                                _assess_ans = await run_blocking(health_agent.llm_complete, _assess_prompt)
                                _assess_ans = (_assess_ans or "").strip()
                                if _assess_ans:
                                    print(f"[assessment] Provided preliminary clinical impression")
                                    for _word in (_assess_ans + " ").split(" "):
                                        if _word:
                                            await websocket.send_text(json.dumps({"type": "token", "content": _word + " "}))
                                    await send_text_message(
                                        websocket, client_state, _assess_ans,
                                        await build_interview_state_async(client_state),
                                        user_response=text_input,
                                    )
                                    continue
                            except Exception as _ae:
                                print(f"[assessment] Error: {_ae} — falling through to graph")

                        if _is_brand or _is_edu:
                            try:
                                from src.graph.nodes.extract import _answer_brand_question, _answer_medical_question

                                if _is_brand:
                                    # Query brandbook ChromaDB (stance_brand_collection)
                                    _ans = await run_blocking(
                                        _answer_brand_question, text_input, health_agent.llm_complete
                                    )
                                else:
                                    # Query Snell's anatomy ChromaDB (single_book_collection)
                                    _ans = await run_blocking(
                                        _answer_medical_question, text_input, health_agent.llm_complete
                                    )

                                if not _ans:
                                    # Pure LLM fallback if RAG returns nothing useful
                                    _fb_type = "brand" if _is_brand else "medical education"
                                    _edu_prompt = f"""You are Sage, a warm and knowledgeable assistant for Stance Health.

Stance Health is India's first technology-enabled MSK (musculoskeletal) health platform,
specialising in physiotherapy, sports rehabilitation, posture correction, and pain management.
"Stance" and "Stance Health" always refer to this clinic.

A patient asked: "{text_input}"

{"IMPORTANT: Do NOT invent specific facts (number of centers, prices, addresses). If unsure, say to visit stancehealth.com." if _is_brand else "Answer with accurate, helpful medical information."}

Answer warmly in 3-5 sentences. Do NOT end with robotic phrases like 'Now let's continue with your assessment' — just answer naturally."""
                                    _ans = await run_blocking(health_agent.llm_complete, _edu_prompt)
                                    _ans = _ans.strip() if _ans else ""

                                if _ans:
                                    print(f"[off_topic] Answered educational/brand question")
                                    for _word in (_ans + " ").split(" "):
                                        if _word:
                                            await websocket.send_text(json.dumps({"type": "token", "content": _word + " "}))
                                    await send_text_message(
                                        websocket, client_state, _ans,
                                        await build_interview_state_async(client_state),
                                        user_response=text_input,
                                    )
                                    continue
                            except Exception as _bre:
                                print(f"[off_topic] Error: {_bre} — falling through to graph")

                        if _session_processing:
                            await websocket.send_text(json.dumps({"type": "error", "text": "Please wait for the previous response."}))
                            continue
                        _session_processing = True
                        try:
                            # ── LANGGRAPH PATH ─────────────────────────────────
                            if _interview_graph is not None and client_state.get("graph_phase") is not None:
                                _turn_t0 = time.perf_counter()
                                print(f"[graph] Processing turn — phase: {client_state.get('graph_phase')}")

                                # Send thought stages so the frontend shows the AgentThoughtStream card
                                _current_sec = client_state.get("graph_current_section", "")
                                if _structured_prom_answers is not None:
                                    _thoughts = [
                                        {"stage": "Reading your response", "detail": "Checking assessment answers", "status": "active"},
                                        {"stage": "Validating responses", "detail": "Structured assessment", "status": "pending"},
                                        {"stage": "Preparing next question", "detail": "", "status": "pending"},
                                    ]
                                else:
                                    _thoughts = [
                                        {"stage": "Reading your response", "detail": f"Processing: {text_input[:40]}...", "status": "active"},
                                        {"stage": "Extracting medical details", "detail": _current_sec or "Present Complaint", "status": "pending"},
                                        {"stage": "Formulating next question", "detail": "", "status": "pending"},
                                    ]
                                await websocket.send_text(json.dumps({
                                    "type": "thought_update",
                                    "thoughts": _thoughts,
                                }))

                                # For PROM sessions: directly record the user's answer into
                                # graph_form before the graph runs.  The extraction LLM knows
                                # nothing about PROM scales, so without this the field stays
                                # empty and _fill_unanswered_fields stamps it
                                # "Not mentioned by patient".
                                _prom_injected_form = None  # set below when answers are injected
                                _structured_prom_bypass = False
                                _prom_answer_updates = {}
                                _pt_meta = _prom_meta_for_input
                                if client_state.get("tagged_form_template"):
                                    if _pt_meta:
                                        import copy as _cp2
                                        _gf = _cp2.deepcopy(client_state.get("graph_form") or {})
                                        _qt_map = client_state.get("tagged_question_texts") or {}

                                        _pt_qids_multi = (_pt_meta or {}).get("question_ids") or []
                                        _pt_qid_single = (_pt_meta or {}).get("question_id")

                                        if _pt_qids_multi:
                                            # Multi-answer turn: structured MCQ submit uses "|" separator.
                                            # Voice/paragraph input has no "|" — extract answers via LLM.
                                            if _structured_prom_answers is not None:
                                                _answers = _structured_prom_answers
                                                _structured_prom_bypass = True
                                            else:
                                                # Natural speech: ask LLM to map the patient's words to
                                                # the correct option for each question.
                                                _voice_qs = [
                                                    _qt_map.get(_qid, _qid) for _qid in _pt_qids_multi if _qid
                                                ]
                                                _voice_opts = [
                                                    ((_pt_meta or {}).get("question_options") or [None]*len(_pt_qids_multi))[_mi2]
                                                    for _mi2 in range(len(_pt_qids_multi))
                                                ]
                                                _voice_prompt = (
                                                    "The patient said:\n" + text_input + "\n\n"
                                                    "Extract one answer per question below. "
                                                    "For questions with options, pick the CLOSEST matching option exactly as written. "
                                                    "If the patient didn't mention the topic, write 'Not mentioned'.\n\n"
                                                )
                                                for _vi, (_vq, _vo) in enumerate(zip(_voice_qs, _voice_opts)):
                                                    _voice_prompt += f"Q{_vi+1}: {_vq}\n"
                                                    if _vo:
                                                        _voice_prompt += f"Options: {', '.join(_vo)}\n"
                                                    _voice_prompt += f"Answer {_vi+1}: "
                                                try:
                                                    _voice_resp = await run_blocking(
                                                        health_agent.llm_complete,
                                                        _voice_prompt,
                                                    )
                                                    # Parse "Answer N: <value>" lines
                                                    import re as _re
                                                    _parsed = _re.findall(r"Answer\s+\d+:\s*(.+)", _voice_resp)
                                                    _answers = [p.strip() for p in _parsed]
                                                    # Pad / trim to match number of questions
                                                    while len(_answers) < len(_pt_qids_multi):
                                                        _answers.append("")
                                                    print(f"[prom] Voice extraction produced {len(_answers)} answers")
                                                except Exception as _ve:
                                                    print(f"[prom] Voice extraction failed: {_ve}")
                                                    _answers = [""] * len(_pt_qids_multi)

                                            for _mi, _mqid in enumerate(_pt_qids_multi):
                                                if not _mqid:
                                                    continue
                                                _manswer = _answers[_mi] if _mi < len(_answers) else ""
                                                if not _manswer or (isinstance(_manswer, str) and _manswer.strip().lower() == "not mentioned"):
                                                    continue
                                                _mscale = "Other Assessments"
                                                for _pfx, _pname in _PROM_SCALE_MAP.items():
                                                    if _mqid.startswith(_pfx + "_"):
                                                        _mscale = _pname
                                                        break
                                                _mfield = _qt_map.get(_mqid) or _prom_field_label(
                                                    _mqid.split("_", 2)[-1] if "_" in _mqid else _mqid
                                                )
                                                if _mscale not in _gf or not isinstance(_gf[_mscale], dict):
                                                    _gf[_mscale] = {}
                                                _gf[_mscale][_mfield] = _manswer
                                                _prom_answer_updates[_mqid] = _manswer
                                                print("[prom] Structured answer persisted")

                                        elif _pt_qid_single:
                                            # Single-question turn: save directly
                                            _pt_scale = "Other Assessments"
                                            for _pfx, _pname in _PROM_SCALE_MAP.items():
                                                if _pt_qid_single.startswith(_pfx + "_"):
                                                    _pt_scale = _pname
                                                    break
                                            _pt_field = (
                                                _qt_map.get(_pt_qid_single)
                                                or _prom_field_label(
                                                    _pt_qid_single.split("_", 2)[-1] if "_" in _pt_qid_single else _pt_qid_single
                                                )
                                            )
                                            if _pt_scale not in _gf or not isinstance(_gf[_pt_scale], dict):
                                                _gf[_pt_scale] = {}
                                            _gf[_pt_scale][_pt_field] = text_input
                                            _prom_answer_updates[_pt_qid_single] = text_input
                                            print("[prom] Answer persisted")

                                        client_state["graph_form"] = _gf
                                        if _prom_answer_updates and client_state.get("prom_snapshot"):
                                            client_state["prom_snapshot"] = apply_prom_answers(
                                                client_state["prom_snapshot"],
                                                _prom_answer_updates,
                                            )
                                        # Immediately persist answers — don't depend on result_state["form"]
                                        # after the graph runs, since graph nodes may not preserve the
                                        # per-question PROM dict structure.
                                        import copy as _cp_prom
                                        _prom_injected_form = _cp_prom.deepcopy(_gf)
                                        await run_blocking(
                                            save_customer_info,
                                            user_id=client_state.get("user_id", ""),
                                            form_data=_prom_injected_form,
                                            current_section="PROM",
                                            form_id=client_state.get("form_id", ""),
                                            preferred_doc_id=client_state.get("prom_source_doc_id"),
                                            attempt_id=client_state.get("attempt_id"),
                                            prom_snapshot=client_state.get("prom_snapshot"),
                                        )

                                # ── Re-ask unanswered PROM questions ──────────────────────────────
                                # After injecting answers, check if any questions were left blank
                                # (voice extraction returned "" or "Not mentioned").  Build a
                                # follow-up tagged turn with only those questions so the patient
                                # gets another chance to answer them.
                                if client_state.get("tagged_form_template") and _pt_meta:
                                    _reask_ids, _reask_qs, _reask_opts, _reask_types, _reask_scales = [], [], [], [], []
                                    _all_qids   = _pt_meta.get("question_ids") or []
                                    _all_qtexts = _pt_meta.get("questions") or []
                                    _all_qopts  = _pt_meta.get("question_options") or []
                                    _all_qtypes = _pt_meta.get("question_types") or []
                                    _all_qscls  = _pt_meta.get("question_scales") or [""] * len(_all_qids)
                                    _qt_map2    = client_state.get("tagged_question_texts") or {}
                                    for _ri, _rqid in enumerate(_all_qids):
                                        if not _rqid:
                                            continue
                                        # Find saved answer in _gf
                                        _rscale = "Other Assessments"
                                        for _rpfx, _rpname in _PROM_SCALE_MAP.items():
                                            if _rqid.startswith(_rpfx + "_"):
                                                _rscale = _rpname
                                                break
                                        _rfield = _qt_map2.get(_rqid) or (_all_qtexts[_ri] if _ri < len(_all_qtexts) else "")
                                        _rval   = (_gf.get(_rscale) or {}).get(_rfield, "")
                                        _rval_s = str(_rval).strip().lower()
                                        if not _rval_s or _rval_s in ("not mentioned", ""):
                                            _reask_ids.append(_rqid)
                                            _reask_qs.append(_all_qtexts[_ri] if _ri < len(_all_qtexts) else _rqid)
                                            _reask_opts.append(_all_qopts[_ri] if _ri < len(_all_qopts) else None)
                                            _reask_types.append(_all_qtypes[_ri] if _ri < len(_all_qtypes) else "text")
                                            _reask_scales.append(_all_qscls[_ri] if _ri < len(_all_qscls) else "")

                                    if _reask_ids:
                                        _reask_preamble = (
                                            "I didn't catch clear answers to the questions below — "
                                            "please tap to select your response for each one:"
                                        )
                                        _reask_combined = _reask_preamble + "\n\n" + "\n".join(
                                            f"{_ri2 + 1}. {_q2}" for _ri2, _q2 in enumerate(_reask_qs)
                                        )
                                        _reask_turn = {
                                            "text": _reask_combined,
                                            "type": "multi_answer",
                                            "question_ids": _reask_ids,
                                            "questions": _reask_qs,
                                            "question_options": _reask_opts,
                                            "question_types": _reask_types,
                                            "question_scales": _reask_scales,
                                        }
                                        _reask_meta = {
                                            "type": "multi_answer",
                                            "options": None,
                                            "question_id": None,
                                            "question_ids": _reask_ids,
                                            "questions": _reask_qs,
                                            "question_options": _reask_opts,
                                            "question_types": _reask_types,
                                            "question_scales": _reask_scales,
                                        }
                                        # Insert the re-ask turn immediately after current index
                                        _cur_idx = client_state.get("tagged_turn_index", 0)
                                        _tt2  = list(client_state.get("tagged_turns") or [])
                                        _ttm2 = list(client_state.get("tagged_turn_metas") or [])
                                        _tt2.insert(_cur_idx, _reask_combined)
                                        _ttm2.insert(_cur_idx, _reask_meta)
                                        client_state["tagged_turns"] = _tt2
                                        client_state["tagged_turn_metas"] = _ttm2
                                        print(f"[prom] Re-ask: {len(_reask_ids)} unanswered questions inserted at idx {_cur_idx}")

                                # Structured PROM submissions are already validated, mapped,
                                # and queued for persistence above. Advancing the pre-built
                                # question list is deterministic, so running general intake
                                # extraction/question generation would add cost without changing
                                # the response or saved values.
                                if _structured_prom_bypass:
                                    _next_index = client_state.get("tagged_turn_index", 0)
                                    response_text, _prom_override_meta, _new_index, _is_complete = advance_tagged_turn(
                                        client_state.get("tagged_turns") or [],
                                        client_state.get("tagged_turn_metas") or [],
                                        _next_index,
                                    )
                                    client_state["tagged_turn_index"] = _new_index

                                    _history = list(client_state.get("graph_history") or [])
                                    _history.extend([
                                        {"role": "user", "message": text_input},
                                        {"role": "agent", "message": response_text},
                                    ])
                                    client_state["graph_history"] = _history

                                    _previous_prom = client_state.get("prom_existing_data") or {}
                                    _prom_template = client_state.get("tagged_form_template") or {}
                                    _all_scales = list({**_previous_prom, **_prom_template}.keys())
                                    _answered_scales = sum(
                                        1 for _scale in _all_scales
                                        if _prom_scale_is_answered(_previous_prom.get(_scale))
                                        or _prom_scale_is_answered((_prom_injected_form or {}).get(_scale))
                                    )
                                    progress = (
                                        round(_answered_scales / len(_all_scales) * 100)
                                        if _all_scales else (100.0 if _is_complete else 0.0)
                                    )
                                    _prom_result_state = {
                                        "form": _prom_injected_form or {},
                                        "current_section": "PROM",
                                        "missing_fields": [],
                                    }
                                    await send_text_message(
                                        websocket,
                                        client_state,
                                        response_text,
                                        await run_blocking(
                                            build_interview_state_from_graph,
                                            _prom_result_state,
                                            client_state,
                                            fetch_form_attachments_fn=fetch_form_attachments,
                                            calculate_form_progress_fn=calculate_form_progress,
                                            calculate_section_completion_status_fn=calculate_section_completion_status,
                                            fetch_form_by_id_fn=None,
                                            progress_override=progress,
                                        ),
                                        user_response=text_input,
                                        question_meta=_prom_override_meta,
                                    )
                                    _log.info(
                                        "prom_structured_turn_complete",
                                        question_count=len(_structured_prom_answers),
                                        complete=_is_complete,
                                    )
                                    if _is_complete:
                                        await run_blocking(
                                            save_customer_info,
                                            user_id=client_state.get("user_id", ""),
                                            form_data=_prom_injected_form or client_state.get("graph_form") or {},
                                            current_section="PROM",
                                            form_id=client_state.get("form_id", ""),
                                            preferred_doc_id=client_state.get("prom_source_doc_id"),
                                            lifecycle_status=FORM_COMPLETED,
                                            attempt_id=client_state.get("attempt_id"),
                                            prom_snapshot=client_state.get("prom_snapshot"),
                                        )
                                    continue

                                client_state["_fetch_form_fn"] = fetch_form_by_id
                                # Capture next PROM turn index BEFORE graph runs so we can
                                # override the response regardless of what the graph generates.
                                _prom_next_turn_idx = (
                                    client_state.get("tagged_turn_index", 0)
                                    if client_state.get("tagged_form_template") else None
                                )
                                graph_state = await run_blocking(
                                    build_graph_state,
                                    client_state,
                                    text_input,
                                    _save_for_graph,
                                )

                                # Attach Langfuse user/session context for this turn
                                _lf_ctx = None
                                if _langfuse_enabled and _langfuse:
                                    try:
                                        from langfuse import propagate_attributes
                                        _lf_ctx = propagate_attributes(
                                            user_id=pseudonymous_id(client_state.get("user_id")) or "anonymous",
                                            session_id=pseudonymous_id(client_state.get("session_id")) or "anonymous",
                                            metadata={
                                                "phase": client_state.get("graph_phase", ""),
                                            },
                                        )
                                        _lf_ctx.__enter__()
                                    except Exception:
                                        _lf_ctx = None

                                # Store user/session on health_agent so llm_complete can
                                # attach them to the Langfuse trace from inside the thread.
                                health_agent._langfuse_user_id = pseudonymous_id(client_state.get("user_id"))
                                health_agent._langfuse_session_id = pseudonymous_id(client_state.get("session_id"))

                                # ── Run graph + send thought updates around it ──
                                # Stream via astream(stream_mode=["messages","values"], version="v2").
                                # "messages" yields LLM tokens as they arrive → frontend shows
                                # words appearing instantly instead of waiting for full response.
                                # "values" gives us the final accumulated state.
                                result_state = None
                                streamed_tokens = []
                                try:
                                    async for chunk in _interview_graph.astream(
                                        graph_state,
                                        stream_mode=["messages", "values"],
                                        version="v2",
                                    ):
                                        if chunk["type"] == "messages":
                                            msg_chunk, meta = chunk["data"]
                                            token = getattr(msg_chunk, "content", "") or ""
                                            # Only stream tokens from response-generating nodes
                                            node = meta.get("langgraph_node", "")
                                            if token and node in ("generate_question", "generate_summary",
                                                                   "handle_summary_response", "apply_correction",
                                                                   "handle_upload_response"):
                                                streamed_tokens.append(token)
                                                # Don't stream tokens during PROM sessions —
                                                # the graph may generate FRM-01 intake questions
                                                # which we override below; streaming them would
                                                # cause a visible flash of wrong content.
                                                if not client_state.get("tagged_form_template"):
                                                    await websocket.send_text(json.dumps({
                                                        "type": "token",
                                                        "content": token,
                                                    }))
                                        elif chunk["type"] == "values":
                                            result_state = chunk["data"]  # accumulate final state
                                except Exception as _stream_err:
                                    print(f"[graph] Streaming failed with {error_type(_stream_err)}; using invoke")
                                    result_state = await run_blocking(
                                        _interview_graph.invoke,
                                        graph_state,
                                    )

                                print(f"[timing] turn_total={(time.perf_counter() - _turn_t0) * 1000:.0f}ms "
                                      f"phase={result_state.get('phase') if result_state else 'unknown'}")

                                if _lf_ctx:
                                    try:
                                        _lf_ctx.__exit__(None, None, None)
                                    except Exception:
                                        pass

                                sync_client_state_from_graph(client_state, result_state)

                                # NOTE: We do NOT sync health_agent.form here.
                                # health_agent is a global singleton — writing to it from
                                # concurrent WebSocket handlers causes cross-user data mixing.
                                # All per-user state lives in client_state["graph_form"] instead.

                                _raw_response = result_state.get("response_text", "") if result_state else "".join(streamed_tokens)
                                # response_text may be a dict in PROM turns (tagged turn object);
                                # normalise to string now so all downstream [:80] slices are safe.
                                response_text = _raw_response if isinstance(_raw_response, str) else (
                                    _raw_response.get("text", "") if isinstance(_raw_response, dict) else str(_raw_response)
                                )
                                _log.info(
                                    "graph_turn_complete",
                                    phase=(result_state.get('phase') if result_state else 'unknown'),
                                    response_chars=len(response_text),
                                )

                                # PROM session override: the graph's generate_question node
                                # may produce FRM-01 intake questions when the user speaks
                                # naturally (e.g. "i have a severe hip pain!"). Ignore the
                                # graph's response_text and serve the correct next tagged turn.
                                _prom_override_meta = None
                                _prom_session_complete = False
                                if _prom_next_turn_idx is not None:
                                    _tt_list = client_state.get("tagged_turns") or []
                                    _tt_metas = client_state.get("tagged_turn_metas") or []
                                    if _prom_next_turn_idx < len(_tt_list):
                                        response_text = _tt_list[_prom_next_turn_idx]
                                        _prom_override_meta = (
                                            _tt_metas[_prom_next_turn_idx]
                                            if _prom_next_turn_idx < len(_tt_metas) else None
                                        )
                                        client_state["tagged_turn_index"] = _prom_next_turn_idx + 1
                                        print(f"[prom] override turn {_prom_next_turn_idx}, next_idx={_prom_next_turn_idx + 1}")
                                    else:
                                        response_text = "Thank you for completing the assessment! Your responses have been recorded."
                                        client_state["tagged_turn_index"] = _prom_next_turn_idx
                                        _prom_session_complete = True

                                # Opt 3: use cached form for progress — no blocking DB fetch per turn
                                _cached_form = result_state.get("form", {})
                                if result_state.get("phase") == "complete":
                                    progress = 100.0
                                elif client_state.get("tagged_form_template"):
                                    # PROM progress: answered / all scales (prev + remaining)
                                    _pp = client_state.get("prom_existing_data") or {}
                                    _pt = client_state["tagged_form_template"]
                                    _all_s = list({**_pp, **_pt}.keys())
                                    _ans_s = sum(1 for k in _all_s if _prom_scale_is_answered(_pp.get(k)) or _prom_scale_is_answered(_cached_form.get(k)))
                                    progress = round(_ans_s / len(_all_s) * 100) if _all_s else 0
                                else:
                                    progress = calculate_form_progress(_cached_form)

                                # Send response to client IMMEDIATELY
                                await send_text_message(
                                    websocket,
                                    client_state,
                                    response_text,
                                    await run_blocking(
                                        build_interview_state_from_graph,
                                        result_state,
                                        client_state,
                                        fetch_form_attachments_fn=fetch_form_attachments,
                                        calculate_form_progress_fn=calculate_form_progress,
                                        calculate_section_completion_status_fn=calculate_section_completion_status,
                                        fetch_form_by_id_fn=fetch_form_by_id,
                                        progress_override=progress,
                                    ),
                                    user_response=text_input,
                                    force_request_attachment=bool(result_state.get("request_attachment")),
                                    question_meta=_prom_override_meta if _prom_override_meta is not None else result_state.get("tagged_question_meta"),
                                )

                                # Persist after sending the response, but await completion so a
                                # disconnect/shutdown cannot silently abandon the clinical save.
                                _save_user_id = client_state.get("user_id", "")
                                _save_form_id = client_state.get("form_id", "")
                                _save_form = result_state.get("form", {})
                                # For PROM forms: answers were already saved immediately after injection
                                # above (in per-question format). Skip the post-graph collapse-save to
                                # avoid overwriting the per-question dict structure with empty strings
                                # if the graph didn't preserve the injected answers in result_state["form"].
                                if client_state.get("tagged_form_template"):
                                    if _prom_session_complete:
                                        await run_blocking(
                                            save_customer_info,
                                            user_id=_save_user_id,
                                            form_data=_prom_injected_form or client_state.get("graph_form") or {},
                                            current_section="PROM",
                                            form_id=_save_form_id,
                                            preferred_doc_id=client_state.get("prom_source_doc_id"),
                                            lifecycle_status=FORM_COMPLETED,
                                            attempt_id=client_state.get("attempt_id"),
                                            prom_snapshot=client_state.get("prom_snapshot"),
                                        )
                                else:
                                    _save_section = result_state.get("current_section", "")
                                    await run_blocking(
                                        _save_for_graph,
                                        _save_user_id, _save_form, _save_section, _save_form_id,
                                        None, None,
                                        FORM_COMPLETED if result_state.get("phase") == "complete" else None,
                                        client_state.get("attempt_id"),
                                    )

                            # ── HEALTHAGENT FALLBACK PATH ───────────────────────
                            else:
                                print(f"[text_input] HealthAgent fallback — talk_mode: {health_agent.talk_mode}")
                                # Send thought update before processing
                                await _send_thought_update(
                                    websocket,
                                    completed_nodes=[],
                                    active_node="extract_form_data",
                                )
                                response_text = await run_blocking(
                                    health_agent.main_processor,
                                    text_input,
                                )
                                # Send thought update after processing
                                await _send_thought_update(
                                    websocket,
                                    completed_nodes=["extract_form_data"],
                                )

                                if response_text == "SKIP_SECTION":
                                    current_index = health_agent.form_sections.index(health_agent.current_section)
                                    if current_index < len(health_agent.form_sections) - 1:
                                        health_agent.current_section = health_agent.form_sections[current_index + 1]
                                        prompt_template = health_agent.make_template(mode="query")
                                        if prompt_template and prompt_template != "SKIP_SECTION":
                                            response_text = await run_blocking(
                                                health_agent.talk_to_user,
                                                prompt_template,
                                            )
                                            health_agent.history.append({"role": "agent", "message": response_text})
                                        else:
                                            response_text = "Let me move on to the next section."
                                    else:
                                        response_text = "Thank you for providing all the information. Let me generate a summary."

                                is_complete = "Thank you for completing all questions" in response_text

                                if health_agent.talk_mode != "START":
                                    health_agent.save_progress()
                                    form_id = client_state.get("form_id")
                                    user_id = client_state.get("user_id")
                                    if form_id and user_id:
                                        existing_form = await run_blocking(
                                            fetch_form_by_id,
                                            form_id,
                                            user_id,
                                            client_state.get("attempt_id"),
                                        )
                                        if existing_form:
                                            form_user_id = existing_form.get("userId")
                                            if str(form_user_id) != str(user_id):
                                                print("[text_input] SECURITY ERROR: form ownership mismatch")
                                                continue
                                    import copy
                                    saved_form_id = await run_blocking(
                                        save_customer_info,
                                        user_id=user_id,
                                        form_data=copy.deepcopy(health_agent.form),
                                        current_section=health_agent.current_section,
                                        form_id=form_id,
                                        lifecycle_status=FORM_COMPLETED if is_complete else None,
                                        attempt_id=client_state.get("attempt_id"),
                                    )
                                    if saved_form_id and not form_id:
                                        client_state["form_id"] = saved_form_id

                                if is_complete:
                                    progress = 100.0
                                else:
                                    form_id = client_state.get("form_id")
                                    user_id = client_state.get("user_id")
                                    if form_id and user_id:
                                        db_form = await run_blocking(
                                            fetch_form_by_id,
                                            form_id,
                                            user_id,
                                            client_state.get("attempt_id"),
                                        )
                                        progress = calculate_form_progress(
                                            db_form["form_data"] if db_form and db_form.get("form_data")
                                            else health_agent.form
                                        )
                                    else:
                                        progress = calculate_form_progress(health_agent.form)

                                await send_text_message(
                                    websocket, client_state, response_text,
                                    await build_interview_state_async(client_state, progress_override=progress),
                                    user_response=text_input,
                                )

                        except Exception as e:
                            err_str = str(e)
                            # 1001 = client navigated away; 1000 = normal close — don't try to send
                            is_disconnect = any(code in err_str for code in ("1001", "1000", "going away", "ConnectionClosed", "disconnect"))
                            if not is_disconnect:
                                print(f"[text_input] Processing failed: {error_type(e)}")
                                try:
                                    await websocket.send_text(
                                        json.dumps({"type": "error", "text": "Unable to process that response. Please try again."})
                                    )
                                except Exception:
                                    pass  # socket already closed
                            else:
                                print(f"[text_input] Client disconnected during processing (code 1001) — skipping error send")
                        finally:
                            _session_processing = False

                    elif msg_type == "audio_end":
                        # Client has finished sending audio
                        client_state["is_recording"] = False

                        # Process the accumulated audio
                        audio_bytes = bytes(client_state["received_audio_buffer"])
                        client_state["received_audio_buffer"].clear()
                        started_at = client_state.get("recording_start_time")
                        elapsed_seconds = (
                            max(0.0, time.monotonic() - started_at)
                            if isinstance(started_at, (int, float))
                            else 0.0
                        )
                        client_state["recording_start_time"] = None
                        try:
                            audio_duration = float(data.get("duration", 0) or 0)
                        except (TypeError, ValueError):
                            audio_duration = 0.0
                        limit_error = audio_limit_error(
                            current_bytes=len(audio_bytes),
                            incoming_bytes=0,
                            elapsed_seconds=elapsed_seconds,
                            max_total_bytes=MAX_AUDIO_SESSION_BYTES,
                            max_duration_seconds=MAX_AUDIO_SESSION_SECONDS,
                            declared_duration_seconds=audio_duration,
                        )
                        if limit_error:
                            await websocket.send_text(
                                json.dumps({"type": "error", "text": limit_error})
                            )
                            continue
                        print(
                            f"Received complete audio: {len(audio_bytes)} bytes, duration: {audio_duration:.2f}s"
                        )

                        # Skip processing if not enough audio data received
                        if (
                            len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5
                        ):  # At least 0.5 seconds
                            print(
                                f"Audio too short ({len(audio_bytes)} bytes), skipping"
                            )
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Audio too short to process",
                                    }
                                )
                            )
                            continue

                        # timestamp still needed downstream for the outbound
                        # 'transcription' WS message; the debug .raw write was removed.
                        timestamp = int(time.time())

                        # Transcribe via Google Cloud Speech-to-Text
                        try:
                            from app.audio.stt import transcribe_audio_bytes, is_hallucination, clean_transcript

                            t_stt_start = time.perf_counter()
                            transcription = await run_blocking(
                                transcribe_audio_bytes, audio_bytes
                            )
                            t_stt_ms = (time.perf_counter() - t_stt_start) * 1000
                            print(f"[audio] STT latency={t_stt_ms:.0f}ms transcript_chars={len(transcription)}")

                            if is_hallucination(transcription):
                                print("[audio] Hallucination detected — discarding transcription")
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "text": "Audio unclear — please try speaking again.",
                                }))
                                continue

                            transcription = clean_transcript(transcription)

                            # Send transcription immediately to frontend for real-time display
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "transcription",
                                        "text": transcription,
                                        "timestamp": timestamp,
                                    }
                                )
                            )

                            # (transcript .txt write removed — never read back)

                            # Log user's speech in the database
                            # MongoDB DISABLED - Commented out
                            # db.log_interaction(
                            #     user_id=client_state["user_id"],
                            #     interview_id=client_state["interview_id"],
                            #     interaction_type="user_speech",
                            #     content=transcription,
                            #     metadata={
                            #         "audio_duration": audio_duration,
                            #         "audio_file": file_path,
                            #         "transcript_file": transcript_path,
                            #     },
                            # )

                            # DO NOT process transcription automatically here
                            # Wait for user to click send button, which will send a text_input message
                            # The text_input handler (line 751) will process it and send the response
                            print(f"Transcription sent to frontend. Waiting for user to click send...")

                        except Exception as e:
                            print(f"[audio] Processing failed: {error_type(e)}")
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error", 
                                        "text": "Unable to process audio. Please try again or type your response."
                                    }
                                )
                            )

                except json.JSONDecodeError:
                    print("[websocket] Invalid JSON message received")
                except Exception as e:
                    print(f"[websocket] Text-message handling failed: {error_type(e)}")

            # Handle binary messages (audio data)
            elif "bytes" in message:
                if client_state.get("auth_context") is None:
                    await websocket.send_text(
                        json.dumps(
                            {"type": "error", "message": "Authentication is required."}
                        )
                    )
                    await websocket.close(code=1008, reason="Authentication required")
                    break
                if client_state["is_recording"]:
                    # Accumulate audio chunks
                    audio_chunk = message["bytes"]
                    started_at = client_state.get("recording_start_time")
                    elapsed_seconds = (
                        max(0.0, time.monotonic() - started_at)
                        if isinstance(started_at, (int, float))
                        else 0.0
                    )
                    limit_error = audio_limit_error(
                        current_bytes=len(client_state["received_audio_buffer"]),
                        incoming_bytes=len(audio_chunk),
                        elapsed_seconds=elapsed_seconds,
                        max_total_bytes=MAX_AUDIO_SESSION_BYTES,
                        max_duration_seconds=MAX_AUDIO_SESSION_SECONDS,
                    )
                    if limit_error:
                        client_state["is_recording"] = False
                        client_state["received_audio_buffer"].clear()
                        client_state["recording_start_time"] = None
                        await websocket.send_text(
                            json.dumps({"type": "error", "text": limit_error})
                        )
                        continue
                    client_state["received_audio_buffer"].extend(audio_chunk)

                    # Log progress
                    total_kb = len(client_state["received_audio_buffer"]) / 1024
                    chunk_kb = len(audio_chunk) / 1024
                    print(
                        f"Received audio chunk: {chunk_kb:.1f} KB, total: {total_kb:.1f} KB"
                    )
                else:
                    print("Received unexpected binary data when not recording")

    except Exception as e:
        print(f"WebSocket failed: {error_type(e)}")
    finally:
        client_state["is_recording"] = False
        client_state["received_audio_buffer"].clear()
        client_state["recording_start_time"] = None
        WS_CONNECTIONS.dec()
        _active_ws_connections.discard(websocket)
        _log.info("ws_disconnected", subject=pseudonymous_id(client_id))
        print("Client disconnected")
        # Close database connection
        # MongoDB DISABLED - Commented out
        # db.close()


def validate_user_exists(user_id: str) -> bool:
    """
    Validate that a user exists in the MongoDB users collection.
    
    Args:
        user_id: The user ID to validate (as string or ObjectId)
        
    Returns:
        True if user exists, False otherwise
    """
    try:
        from bson import ObjectId
        
        # Ensure MongoDB connection
        if users_collection is None:
            print(f"[validate_user_exists] MongoDB not connected")
            return False
        
        # Get database and collection names for logging
        db_name = users_collection.database.name
        collection_name = users_collection.name
        
        # Convert user_id to ObjectId if it's a valid ObjectId string
        try:
            user_object_id = ObjectId(user_id) if isinstance(user_id, str) else user_id
        except Exception:
            print("[validate_user_exists] Invalid subject identifier format")
            return False
        
        # Check if user exists
        user = users_collection.find_one({"_id": user_object_id})
        exists = user is not None
        
        if exists:
            print(f"[validate_user_exists] Subject exists in {db_name}.{collection_name}")
        else:
            print(f"[validate_user_exists] Subject not found in {db_name}.{collection_name}")
        
        return exists
        
    except Exception as e:
        print(f"[validate_user_exists] Validation failed: {error_type(e)}")
        return False


def ensure_users_collection() -> Collection:
    """Get an initialized users collection or raise."""
    global users_collection

    if users_collection is None:
        # Attempt to reconnect once to avoid stale connections
        init_mongo()

    if users_collection is None:
        raise RuntimeError("User directory is not configured on the server.")

    return users_collection


def _fetch_users_sync(collection: Collection, mongo_query: dict, limit: int) -> list[dict]:
    """Execute and consume the synchronous PyMongo cursor off the event loop."""

    cursor = collection.find(mongo_query).sort("updatedAt", -1).limit(limit)
    users = []
    for doc in cursor:
        serialized = serialize_user(doc)
        if serialized and serialized["id"]:
            users.append(serialized)
    return users


@app.get("/api/users")
async def get_users(
    search: str = Query(default="", description="Optional search term"),
    limit: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(None),
):
    """Return up-to-date list of users for the selector."""
    try:
        context = _http_auth_context(authorization)
        _authorize_directory_http(context)
        collection = await run_blocking(ensure_users_collection)

        mongo_query = {}
        if search:
            regex = {"$regex": search, "$options": "i"}
            mongo_query = {
                "$or": [
                    {"firstName": regex},
                    {"lastName": regex},
                    {"name": regex},
                    {"email": regex},
                ]
            }

        users = await run_blocking(_fetch_users_sync, collection, mongo_query, limit)

        return {"users": users}
    except HTTPException:
        raise
    except RuntimeError as err:
        raise HTTPException(
            status_code=500,
            detail=str(err),
        ) from err
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch users: {str(e)}"
        )


@app.get("/api/users/{user_id}/consent")
async def get_consent_status(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    """
    Check whether a user has accepted the consent policy.
    Checks two sources:
    1. users.profileData.consentAccepted (set by our API or legacy)
    2. consentrecords collection (written by consent.stance.health via recordConsent GraphQL mutation)
    """
    try:
        context = _http_auth_context(authorization)
        _authorize_patient_http(context, user_id, "consent:read")
        from app.config import MONGO_DB_NAME
        users_col = await run_blocking(ensure_users_collection)
        user_oid = ObjectId(user_id)

        # Source 1: users.profileData.consentAccepted
        user = await run_blocking(
            users_col.find_one,
            {"_id": user_oid},
            {"profileData.consentAccepted": 1, "profileData.consentAcceptedAt": 1}
        )
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        profile = user.get("profileData", {})
        if profile.get("consentAccepted"):
            return {
                "consentAccepted": True,
                "source": "profileData",
                "consentAcceptedAt": str(profile.get("consentAcceptedAt", "")),
            }

        # Source 2: consentrecords collection (created by consent.stance.health)
        try:
            db = users_col.database
            consent_col = db["consentrecords"]
            record = await run_blocking(
                consent_col.find_one,
                {"userId": user_oid, "isActive": True},
                {"acceptedAt": 1}
            )
            if record:
                return {
                    "consentAccepted": True,
                    "source": "consentrecords",
                    "consentAcceptedAt": str(record.get("acceptedAt", "")),
                }
        except Exception as ce:
            print(f"[consent] consentrecords check failed (non-fatal): {ce}")

        return {"consentAccepted": False}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check consent: {str(e)}")


@app.post("/api/users/{user_id}/consent")
async def accept_consent(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    """Record that a user has accepted the consent policy."""
    try:
        context = _http_auth_context(authorization)
        _authorize_patient_http(context, user_id, "consent:write")
        from datetime import datetime, timezone
        collection = await run_blocking(ensure_users_collection)
        user_oid = ObjectId(user_id)
        result = await run_blocking(
            collection.update_one,
            {"_id": user_oid},
            {"$set": {
                "profileData.consentAccepted": True,
                "profileData.consentAcceptedAt": datetime.now(timezone.utc),
                "profileData.consentPolicyVersion": "1.1.0",
            }}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"success": True, "consentAccepted": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to record consent: {str(e)}")


@app.get("/api/users/{user_id}/forms")
async def get_user_forms_endpoint(
    user_id: str,
    authorization: Optional[str] = Header(None),
):
    """Return all forms for a specific user."""
    try:
        context = _http_auth_context(authorization)
        _authorize_patient_http(context, user_id, "forms:read")
        forms = await run_blocking(fetch_user_forms, user_id)
        return {"forms": forms}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch user forms: {str(e)}"
        )


@app.get("/api/forms/{form_id}")
async def get_form_endpoint(
    form_id: str,
    userId: str = Query(..., description="User ID (required)"),
    attemptId: Optional[str] = Query(None, description="Form attempt ID"),
    authorization: Optional[str] = Header(None),
):
    """Return a specific form by form_id and userId."""
    try:
        context = _http_auth_context(authorization)
        _authorize_patient_http(context, userId, "forms:read")
        form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
        if form is None:
            raise HTTPException(status_code=404, detail="Form not found")
        return {"form": form}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch form: {str(e)}"
        )


@app.get("/api/forms/{form_id}/progress")
async def get_form_progress_endpoint(
    form_id: str,
    userId: str = Query(..., description="User ID (required)"),
    attemptId: Optional[str] = Query(None, description="Form attempt ID"),
    authorization: Optional[str] = Header(None),
):
    """
    Return section completion status for a form.
    Sections are ordered with completed sections first, incomplete sections at the bottom.
    """
    try:
        context = _http_auth_context(authorization)
        _authorize_patient_http(context, userId, "forms:read")
        form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
        if form is None:
            raise HTTPException(status_code=404, detail="Form not found")
        
        form_data = form.get("form_data", {})
        progress_data = calculate_section_completion_status(form_data)
        
        return progress_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to calculate form progress: {str(e)}"
        )


@app.post("/api/forms/{form_id}/attachments")
async def upload_form_attachment(
    form_id: str,
    files: List[UploadFile] = File(...),
    userId: str = Form(...),
    attemptId: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
):
    """Upload multiple scans/reports to S3, generate combined summary, and auto-fill Reports section."""
    context = _http_auth_context(authorization)
    _authorize_patient_http(context, userId, "forms:write")

    if not is_s3_configured():
        raise HTTPException(
            status_code=500,
            detail="S3 is not configured on the server.",
        )

    form = await run_blocking(fetch_form_by_id, form_id, userId, attemptId)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found.")

    # Enforce that each form remains bound to a single user.
    # Compare as strings to handle ObjectId vs string mismatches.
    if str(form.get("userId", "")) != str(userId):
        raise HTTPException(
            status_code=403,
            detail="Form does not belong to this user.",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")
    if len(files) > MAX_ATTACHMENT_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"A maximum of {MAX_ATTACHMENT_FILES} files is allowed per request.",
        )

    max_bytes = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
    max_total_bytes = MAX_ATTACHMENT_TOTAL_MB * 1024 * 1024
    validated_files = []
    total_pages = 0

    # Read and validate the complete request before creating any S3 objects.
    # This prevents a late invalid/oversized file from leaving partial uploads.
    for file_index, file in enumerate(files, start=1):
        filename = file.filename or "upload.bin"
        current_total = sum(len(item[0]) for item in validated_files)
        remaining_total = max_total_bytes - current_total
        try:
            if remaining_total <= 0:
                raise ValueError("Combined files exceed the request size limit")
            read_limit = min(max_bytes, remaining_total)
            try:
                file_bytes = await read_upload_bounded(file, max_bytes=read_limit)
            except ValueError as exc:
                if remaining_total < max_bytes:
                    raise ValueError(
                        "Combined files exceed the request size limit"
                    ) from exc
                raise ValueError("File exceeds the per-file size limit") from exc
            validate_upload_quotas(
                [len(file_bytes)],
                max_files=1,
                max_file_bytes=max_bytes,
                max_total_bytes=max_bytes,
            )
            running_total = current_total + len(file_bytes)
            if running_total > max_total_bytes:
                raise ValueError("Combined files exceed the request size limit")
            inspection = await run_blocking(
                inspect_report_upload,
                file_bytes,
                filename,
                file.content_type,
            )
            total_pages += inspection.page_count
            if total_pages > MAX_REPORT_TOTAL_PAGES:
                raise ValueError(
                    f"Combined reports exceed the {MAX_REPORT_TOTAL_PAGES}-page limit"
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"File {file_index} was rejected: {exc}",
            ) from exc
        except Exception as exc:
            print(f"[upload_attachment] Content inspection failed: {error_type(exc)}")
            raise HTTPException(
                status_code=400,
                detail=f"File {file_index} is corrupt or cannot be decoded.",
            ) from exc

        validated_files.append(
            (file_bytes, filename, inspection.identity.content_type)
        )

    try:
        validate_upload_quotas(
            (len(item[0]) for item in validated_files),
            max_files=MAX_ATTACHMENT_FILES,
            max_file_bytes=max_bytes,
            max_total_bytes=max_total_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if report_jobs_collection is None:
        await run_blocking(init_mongo)
    if report_jobs_collection is None:
        raise HTTPException(status_code=500, detail="Report queue is unavailable.")
    active_report_jobs = await run_blocking(
        report_jobs_collection.count_documents,
        {"status": {"$in": ["queued", "retry", "processing"]}},
        limit=REPORT_JOB_MAX_BACKLOG,
    )
    if active_report_jobs >= REPORT_JOB_MAX_BACKLOG:
        raise HTTPException(
            status_code=503,
            detail="Report processing is at capacity. Please retry later.",
        )

    report_objects = []
    attachment_records = []

    # Upload only after the entire batch has passed preflight validation.
    for file_bytes, filename, content_type in validated_files:
        key = generate_object_key(userId, form_id, filename)
        
        try:
            s3_url = await run_blocking(
                upload_bytes_to_s3,
                file_bytes,
                key,
                content_type,
            )
            print(f"[upload_attachment] Uploaded object ({len(file_bytes)} bytes)")
        except RuntimeError as exc:
            print(f"[upload_attachment] S3 upload failed: {error_type(exc)}")
            continue

        report_objects.append({"key": key, "filename": filename})
        
        attachment_records.append({
            "id": str(uuid.uuid4()),
            "type": "report",
            "label": "Medical Report",
            "fileName": filename,
            "url": s3_url,
            "uploadedAt": datetime.now(timezone.utc).isoformat(),
            "contentType": content_type,
        })

    if not report_objects:
        raise HTTPException(status_code=400, detail="No valid files were uploaded.")

    # ── Step 1: Save attachment records to MongoDB immediately ────────────────
    if customer_info_collection is None or report_jobs_collection is None:
        await run_blocking(init_mongo)
    if customer_info_collection is None or report_jobs_collection is None:
        raise HTTPException(status_code=500, detail="Database unavailable.")

    owned_form_filter = build_owned_form_filter(form, userId, form_id)
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    form_data = form.get("form_data", {})
    hist_diag = form_data.get("History & Diagnostics", {})
    processing_text = (
        f"Uploaded {len(attachment_records)} document(s). "
        "Report summary processing is in progress."
    )
    update_fields = {
        "reportProcessing": {
            "jobId": job_id,
            "status": "queued",
            "queuedAt": now,
        },
        "updatedAt": now,
    }
    if not str(hist_diag.get("Reports", "")).strip():
        update_fields["form_data.History & Diagnostics.Reports"] = processing_text
        form_data.setdefault("History & Diagnostics", {})["Reports"] = processing_text

    await run_blocking(
        customer_info_collection.update_one,
        owned_form_filter,
        {
            "$push": {"attachments": {"$each": attachment_records}},
            "$set": update_fields,
        },
    )
    print(f"[upload_attachment] Saved {len(attachment_records)} attachment(s) to MongoDB")

    try:
        job = build_report_job(
            job_id=job_id,
            user_id=owned_form_filter["userId"],
            form_id=form_id,
            objects=report_objects,
            now=now,
        )
        await run_blocking(
            report_jobs_collection.insert_one,
            job,
        )
    except Exception as exc:
        await run_blocking(
            customer_info_collection.update_one,
            {
                "formId": form_id,
                "userId": owned_form_filter["userId"],
                "reportProcessing.jobId": job_id,
            },
            {
                "$set": {
                    "reportProcessing.status": "enqueue_failed",
                    "reportProcessing.failedAt": datetime.now(timezone.utc),
                }
            },
        )
        _log.error("report_job_enqueue_failed", error_type=error_type(exc))
        raise HTTPException(
            status_code=503,
            detail="Reports were uploaded, but summary processing could not be queued.",
        ) from exc

    updated_progress = calculate_form_progress(form_data)
    section_progress = calculate_section_completion_status(form_data)

    return {
        "attachments": attachment_records,
        "reports_filled": True,
        "ocr_status": "processing",
        "ocr_job_id": job_id,
        "form_data": form_data,
        "progress": updated_progress,
        "sectionProgress": section_progress,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
