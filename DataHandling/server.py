from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
# faster_whisper removed — STT now uses Google Cloud Speech-to-Text (see app/audio/stt.py)
import time
import os
import json
from typing import Optional
from dotenv import load_dotenv
load_dotenv()

# Import HealthAgent and related functionalities
from src.llm.functionalities import HealthAgent

# Centralized configuration (env vars, constants, paths).
# Aliased to the legacy names used throughout this file so handler code is
# unchanged. New code should import directly from app.config.
from app.config import (
    CORS_ALLOWED_ORIGINS,
    AUDIO_RATE as RATE,
    AUDIO_SAMPLE_WIDTH as SAMPLE_WIDTH,
    MAX_AUDIO_SESSION_BYTES,
    MAX_AUDIO_SESSION_SECONDS,
    DEFAULT_FORM_ID,
    GEMINI_REQUEST_TIMEOUT_MS,
    CHAT_HISTORY_RETENTION_DAYS,
)
from app.db.mongo import init_mongo, mongo
from app.db.users import validate_user_exists
from app.ai.models import MODEL_REGISTRY
from app.observability.ai_usage import gemini_usage, tracked_ai_call
from app.audio.limits import audio_limit_error
from app.content_safety import contains_internal_transcription_prompt
from app.runtime.blocking import run_blocking
from app.jobs.worker import _report_worker_loop
from app.api.forms import register_form_routes
from app.api.health import register_health_routes
from app.api.users import register_user_routes
from app.forms.progress import (
    calculate_form_progress,
    calculate_section_completion_status,
)
from app.forms.lifecycle import (
    COMPLETED as FORM_COMPLETED,
    has_meaningful_form_data,
    is_completed_form,
)
from app.forms.prom import (
    PROM_SCALE_MAP as _PROM_SCALE_MAP,
    advance_tagged_turn,
    parse_structured_prom_answers,
    prom_field_label as _prom_field_label,
    prom_scale_is_answered as _prom_scale_is_answered,
)
from app.forms.questions import batch_tagged_questions, personalize_questions
from app.forms.repository import (
    create_placeholder_form,
    fetch_form_attachments,
    fetch_form_by_id,
    save_customer_info,
)
from app.interview.session import reset_health_agent_for_new_interview
from app.ws.completion import send_completed_form
from app.ws.handlers.control import (
    handle_audio_start,
    handle_end_session,
    handle_ping,
)
from app.ws.thoughts import send_thought_update as _send_thought_update
from app.forms.instruments import (
    apply_prom_answers,
    build_prom_snapshot,
    questions_from_prom_snapshot,
)
from app.clinical.escalation import (
    assess_urgent_risk,
    record_escalation,
)
from app.clinical.scope import assess_msk_intake_scope
from app.audit.chat_history import record_chat_message
from src.graph.pure_functions.clarification import (
    boundary_response_with_pending_question,
    build_out_of_flow_response,
    pending_intake_question,
)
from app.ws.idempotency import RecentRequestWindow, RequestDecision
from app.observability.privacy import error_type, pseudonymous_id
from app.observability.logging import log as _log
from app.observability.metrics import WS_CONNECTIONS, install_metrics
from app.observability.tracing import (
    langfuse_client as _langfuse,
    langfuse_enabled as _langfuse_enabled,
)
from app.security.input import sanitize_patient_input
from app.security.rate_limit import RateLimiter
# Pure stateless helpers. Aliased to legacy names used throughout this file.
from app.db.ownership import build_owned_form_filter
from app.db.queries import (
    build_user_form_queries,
    collection_namespace,
    empty_tagged_questions_result,
)

# Import MongoDB database connector
# MongoDB DISABLED - Commented out
# from db import MedicalInterviewDB

_rate_limiter = RateLimiter(max_per_minute=30)
_request_window = RecentRequestWindow(capacity=4096)

# ── Active connections tracker for graceful shutdown ─────────────────────────
_active_ws_connections: set = set()
_shutting_down = False
_report_worker_task: asyncio.Task | None = None
_report_worker_stop: asyncio.Event | None = None

# ── FastAPI app with lifespan for graceful startup/shutdown ──────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance):
    global _shutting_down, _report_worker_task, _report_worker_stop
    _log.info("server_startup", service="healthflex-agent")
    _shutting_down = False
    _report_worker_stop = asyncio.Event()
    if mongo.report_jobs_collection is not None:
        _report_worker_task = asyncio.create_task(
            _report_worker_loop(_report_worker_stop)
        )
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

install_metrics(app)

# ── Agent thought stream: human-readable labels for each graph node ──────────


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

    # Initialize reasoning-grade Gemini model for form extraction.
    # Kept separate from the main conversation model so only extraction pays
    # for the more capable reasoning configuration.
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
            # The configured reasoning model may enable thinking by default;
            # disable it here to keep extraction latency bounded.
            try:
                from google import genai as _genai_r
                from google.genai import types as _genai_r_types
                # The extraction request runs in parallel with question
                # generation.  Bound its network wait so one unavailable
                # provider connection cannot keep a patient response pending
                # forever; reasoning_extract then falls back to the normal
                # interview model's extraction path.
                _r_client = _genai_r.Client(
                    api_key=_api_key,
                    http_options=_genai_r_types.HttpOptions(
                        timeout=GEMINI_REQUEST_TIMEOUT_MS,
                    ),
                )
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




# _serialize_datetime and serialize_user moved to app.db.serializers (imported above).





def fetch_tagged_questions(user_id: str, form_id: str) -> tuple:
    """
    Return (resolved_texts, prom_form_template, source_document_id) for the
    exact (user_id, form_id). Returns ([], {}, None) if nothing is found.
    """
    if mongo.tagged_questions_collection is None:
        return empty_tagged_questions_result()
    try:
        queries = build_user_form_queries(user_id, form_id)

        doc = None
        _from_customer_info = False
        for q in queries:
            doc = mongo.tagged_questions_collection.find_one(q)
            if doc:
                break

        # Fallback: check mongo.customer_info_collection for template docs that embed the
        # questions array directly (new template format from the clinic admin system)
        if not doc and mongo.customer_info_collection is not None:
            for q in queries:
                doc = mongo.customer_info_collection.find_one(q)
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
                qb = mongo.tagged_questions_collection.database["question-bank"]
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


def _record_chat_audit(
    client_state: dict,
    *,
    role: str,
    content: str,
    message_type: str = "text_message",
    request_id: object = None,
    question_id: object = None,
    error_code: object = None,
    phase: object = None,
    section: object = None,
) -> bool:
    """Persist one short-lived transcript message without exposing it to logs."""

    return record_chat_message(
        mongo.chat_history_collection,
        user_id=client_state.get("user_id"),
        session_id=client_state.get("session_id"),
        form_id=client_state.get("form_id"),
        attempt_id=client_state.get("attempt_id"),
        interview_id=client_state.get("interview_id"),
        role=role,
        content=content,
        message_type=message_type,
        phase=phase if phase is not None else client_state.get("graph_phase"),
        section=(
            section
            if section is not None
            else client_state.get("graph_current_section")
        ),
        request_id=request_id,
        question_id=question_id,
        error_code=error_code,
        retention_days=CHAT_HISTORY_RETENTION_DAYS,
    )


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
    if interview_state and interview_state.get("status") == FORM_COMPLETED:
        # Completion during an active session uses a final text_message. Lock
        # server-side too, so already queued patient messages cannot call AI.
        client_state["attempt_locked"] = True
        client_state["locked_form"] = {
            "formId": client_state.get("form_id"),
            "attemptId": client_state.get("attempt_id"),
            "status": FORM_COMPLETED,
        }
        requires_attachment = False
    elif force_request_attachment:
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
    await run_blocking(
        _record_chat_audit,
        client_state,
        role="agent",
        content=text,
        message_type="text_message",
        question_id=(question_meta or {}).get("questionId"),
        phase=resolved_interview_state.get("status"),
        section=resolved_interview_state.get("section"),
    )






# Initialize database
# MongoDB DISABLED - Commented out
# print("Connecting to MongoDB...")
# db = MedicalInterviewDB()
# print("MongoDB connected")
init_mongo()


register_health_routes(app, lambda: _interview_graph is not None)
register_user_routes(app)
register_form_routes(app)


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
        # Terminal attempts are read-only. This server-side flag blocks text,
        # audio, end-session saves, and patient-created replacement attempts.
        "attempt_locked": False,
        "locked_form": None,
        "prom_snapshot": None,
        "prom_source_doc_id": None,
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

                    if msg_type == "ping":
                        await handle_ping(websocket)
                        continue

                    # A completed attempt remains read-only even if an outdated
                    # or modified client tries to send input after receiving the
                    # lock response. Starting/loading a clinician-assigned attempt
                    # remains possible on the same socket.
                    if (
                        client_state.get("attempt_locked")
                        and msg_type not in {"start_interview", "load_form"}
                    ):
                        await send_completed_form(
                            websocket,
                            client_state,
                            client_state.get("locked_form") or {},
                        )
                        continue

                    if msg_type == "start_interview":
                        # Client is ready to start the interview (after login or page reload)
                        from src.prompts import (
                            INITIAL_INTAKE_PROMPT,
                        )

                        # Get user_id from the message if provided
                        provided_user_id = data.get("userId")
                        # Both automatic startup and the UI retry can arrive on
                        # the same socket. A successful startup is emitted once;
                        # a new socket has fresh client_state and can resume.
                        if (
                            client_state.get("startup_sent")
                            and provided_user_id == client_state.get("user_id")
                            and (data.get("formId") or DEFAULT_FORM_ID) == client_state.get("form_id")
                            and (
                                not data.get("attemptId")
                                or data.get("attemptId") == client_state.get("attempt_id")
                            )
                        ):
                            continue
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

                        # Validate that the user exists in the database
                        if not await run_blocking(validate_user_exists, provided_user_id):
                            # Get database info for error message
                            db_name, collection_name = collection_namespace(
                                mongo.users_collection, "users"
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
                        client_state["attempt_locked"] = False
                        client_state["locked_form"] = None
                        client_state["prom_snapshot"] = None
                        client_state["prom_source_doc_id"] = None
                        # Initialize form_data early so it's always defined regardless of
                        # resume vs new-interview path (avoids UnboundLocalError).
                        form_data = {}

                        # Resolve and lock a terminal attempt before loading PROM
                        # definitions, personalizing questions, generating a summary,
                        # or making any other billable AI/provider call. With no
                        # attemptId, this resolves the latest attempt for this exact
                        # patient + form template. A clinician-assigned draft therefore
                        # resumes, while a completed latest attempt remains locked.
                        _start_existing_form = await run_blocking(
                            fetch_form_by_id,
                            provided_form_id,
                            provided_user_id,
                            provided_attempt_id,
                        )
                        if is_completed_form(_start_existing_form):
                            _log.info(
                                "completed_form_reopen_blocked",
                                subject=pseudonymous_id(provided_user_id),
                                form_id=provided_form_id,
                            )
                            await send_completed_form(
                                websocket,
                                client_state,
                                _start_existing_form,
                            )
                            continue

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
                            existing_form = _start_existing_form

                        if provided_attempt_id and not existing_form:
                            # Recover IDs generated by the old frontend but not
                            # persisted by the old backend, only for a patient's
                            # first-ever form. If any form already exists, an unknown
                            # attempt remains invalid and cannot bypass consultant
                            # assignment or completed-form locking.
                            _any_existing_attempt = await run_blocking(
                                fetch_form_by_id,
                                provided_form_id,
                                provided_user_id,
                                None,
                            )
                            if _any_existing_attempt:
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "error",
                                            "code": "ATTEMPT_NOT_FOUND",
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
                                if provided_form_id == DEFAULT_FORM_ID:
                                    from src.forms.loader import load_form as _load_resume_form
                                    _resume_template = _load_resume_form("FRM-01").empty_form()
                                    for _section, _fields in _resume_template.items():
                                        _stored_fields = form_data.get(_section, {})
                                        if isinstance(_stored_fields, dict):
                                            _fields.update(_stored_fields)
                                    for _section, _fields in form_data.items():
                                        if _section.startswith("Additional Complaint ") and isinstance(_fields, dict):
                                            _resume_template[_section] = dict(_fields)
                                    form_data = _resume_template
                                current_section = existing_form.get("current_section", "Present Complaint")

                                # Legacy drafts may have been populated by the old
                                # free-form flow before the MSK scope boundary
                                # existed. Validate saved content before showing a
                                # welcome, summary, or next MSK question; otherwise
                                # a fever/cold draft can be re-presented as an MSK
                                # interview after every reconnect.
                                _saved_patient_content = "\n".join(
                                    str(value)
                                    for section in form_data.values()
                                    if isinstance(section, dict)
                                    for value in section.values()
                                    if value and str(value).strip()
                                )
                                # Scope is advisory. Ask for visit clarification in
                                # chat; never stop the socket for a non-MSK answer.
                                # Use the complaint alone, not allergies/past history.
                                _saved_scope = assess_msk_intake_scope(
                                    form_data.get("Present Complaint", {}).get("Primary Complaint", "")
                                ) if provided_form_id == DEFAULT_FORM_ID else None

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
                                client_state["graph_current_section"] = _resume_section

                                # Derive the correct question_round from section completion so the
                                # graph asks questions at the right depth (not restarting from round 0).
                                _idx = _resume_idx
                                client_state["graph_question_round"] = _idx if _idx < 3 else 2
                                client_state["graph_referral_asked"] = referral_complete

                                # Derive the ordered form_sections list
                                client_state["graph_form_sections"] = list(form_data.keys())

                                print(f"[start_interview] Loaded form at section index {_resume_idx}")

                                # Resume must be deterministic. Do not call the
                                # legacy HealthAgent summary/question path here:
                                # it can invent facts and duplicate a summary on
                                # every reconnect. Use the same required-field
                                # plan as a live LangGraph turn.
                                _required_missing = [
                                    (section, field)
                                    for section in client_state["graph_form_sections"]
                                    for field in _vs(form_data, section)
                                ]
                                if _saved_scope is not None:
                                    resume_message = _saved_scope.patient_message
                                    client_state["scope_notice_sent"] = True
                                elif not _saved_patient_content:
                                    resume_message = INITIAL_INTAKE_PROMPT
                                elif not _required_missing:
                                    resume_message = (
                                        "All required answers for this assessment are already saved. "
                                        "Please ask your clinician to review the assessment."
                                    )
                                else:
                                    from src.graph.pure_functions.question_plan import contextual_resume_question
                                    from src.forms.loader import load_form as _load_resume_form
                                    resume_message = contextual_resume_question(
                                        form_data,
                                        _required_missing,
                                        _load_resume_form("FRM-01").field_labels,
                                    )

                                await send_text_message(
                                    websocket,
                                    client_state,
                                    resume_message,
                                    await build_interview_state_async(client_state),
                                    user_response=None
                                )
                                client_state["startup_sent"] = True

                                # Keep graph history/phase aligned with messages
                                # emitted by this resume path outside LangGraph.
                                client_state["graph_history"] = [
                                    {"role": "agent", "message": resume_message}
                                ]
                                if not _saved_patient_content and not client_state.get("tagged_turns"):
                                    # This reserved-but-empty attempt is clinically
                                    # a brand-new FRM-01 interview.  The one opening
                                    # question above is all the patient should see;
                                    # their reply goes through normal extraction.
                                    client_state["graph_phase"] = "interviewing"
                                    continue
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
                                    provided_attempt_id,
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

                            # A brand-new attempt legitimately has an empty dict.
                            # Do not reference loop variables after scanning it:
                            # they do not exist when the dict is empty, which used
                            # to abort startup before welcome/question delivery.
                            form_has_data = has_meaningful_form_data(form_data)
                            if form_has_data:
                                print("[start_interview] Agent form contained unexpected data; resetting")
                                health_agent.init_form()

                        # For a new FRM-01 interview, send one open question only.
                        # PROM forms retain their assessment-specific introduction
                        # and first structured question.
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
                            _welcome_text = INITIAL_INTAKE_PROMPT
                            client_state["graph_history"] = [
                                {"role": "agent", "message": _welcome_text}
                            ]
                        await send_text_message(
                            websocket,
                            client_state,
                            _welcome_text,
                            await build_interview_state_async(
                                client_state, progress_override=progress, use_db_form=True
                            ),
                            user_response=None
                        )

                        client_state["startup_sent"] = True
                        if _tagged_turns:
                            # PROM assessments are already clinician-selected and
                            # can show their first structured question immediately.
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
                        # The public patient socket must never allocate a new
                        # assessment. A consultant/dashboard assignment creates
                        # the attempt first; the patient can then open that exact
                        # attempt through start_interview.
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "text": (
                                "A new assessment must be assigned by your clinician. "
                                "Please use the new assessment link they provide."
                            ),
                            "code": "CLINICIAN_ASSIGNMENT_REQUIRED",
                        }))
                        continue

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
                                        if mongo.customer_info_collection is not None:
                                            delete_filter = build_owned_form_filter(
                                                existing_form,
                                                user_id_for_check,
                                                existing_form_id,
                                            )
                                            result = await run_blocking(
                                                mongo.customer_info_collection.delete_one,
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

                        # Loading a completed record is a read-only operation. Do
                        # not hydrate it into the interview agent or regenerate a
                        # summary, because either action would reopen the terminal
                        # attempt and incur unnecessary AI usage.
                        if is_completed_form(form):
                            _log.info(
                                "completed_form_load_blocked",
                                subject=pseudonymous_id(current_user_id),
                                form_id=form_id,
                            )
                            await send_completed_form(websocket, client_state, form)
                            continue

                        client_state["attempt_locked"] = False
                        client_state["locked_form"] = None
                        
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
                        await handle_end_session(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            save_customer_info=save_customer_info,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                            calculate_form_progress=calculate_form_progress,
                            run_blocking=run_blocking,
                        )

                    elif msg_type == "audio_start":
                        handle_audio_start(client_state)

                    elif msg_type == "text_input":
                        # Client is sending text input (manual or auto-sent transcription)
                        text_input = data.get("text", "").strip()
                        if not text_input:
                            continue
                        if contains_internal_transcription_prompt(text_input):
                            _log.warning(
                                "internal_prompt_content_rejected_from_text_input",
                                subject=pseudonymous_id(client_state.get("user_id")),
                            )
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "text": "The transcription could not be processed safely. Please record it again or type your response.",
                            }))
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
                        await run_blocking(
                            _record_chat_audit,
                            client_state,
                            role="patient",
                            content=text_input,
                            message_type="text_input",
                            request_id=data.get("requestId"),
                            question_id=data.get("questionId"),
                        )

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
                            if mongo.clinical_escalations_collection is not None:
                                try:
                                    await run_blocking(
                                        record_escalation,
                                        mongo.clinical_escalations_collection,
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

                        # Scope boundary: a clear non-MSK presentation must not
                        # be extracted into the MSK form or summarized as an MSK
                        # assessment.  This comes after urgent-risk detection so
                        # emergency handling always takes priority.
                        _scope_decision = assess_msk_intake_scope(text_input)
                        _existing_complaint = (client_state.get("graph_form") or {}).get(
                            "Present Complaint", {}
                        ).get("Primary Complaint", "")
                        # Clarify only an initial visit description. Later history
                        # answers must not interrupt an established interview.
                        if _existing_complaint or client_state.get("scope_notice_sent") or client_state.get("form_id") != DEFAULT_FORM_ID:
                            _scope_decision = None
                        if _scope_decision is not None:
                            _log.info(
                                "clinical_scope_clarification",
                                subject=pseudonymous_id(client_state.get("user_id")),
                                category=_scope_decision.category,
                                policy_version=_scope_decision.policy_version,
                            )
                            client_state["scope_notice_sent"] = True
                            client_state["graph_history"] = list(client_state.get("graph_history") or []) + [
                                {"role": "user", "message": text_input, "clinical_extraction": False},
                                {"role": "agent", "message": _scope_decision.patient_message, "clinical_extraction": False},
                            ]
                            await send_text_message(
                                websocket, client_state, _scope_decision.patient_message,
                                await build_interview_state_async(client_state), user_response=None,
                            )
                            continue

                        # Global interview boundary. Out-of-flow, medical-advice,
                        # medication and return-to-activity questions receive one
                        # fixed response without changing form state or invoking AI.
                        _last_agent_question = pending_intake_question(
                            client_state.get("graph_history") or []
                        )
                        _boundary_response = (
                            None
                            if _structured_prom_answers is not None
                            else build_out_of_flow_response(
                                text_input, _last_agent_question
                            )
                        )
                        if _boundary_response:
                            _boundary_response = boundary_response_with_pending_question(
                                _boundary_response, _last_agent_question
                            )
                            client_state["graph_history"] = list(
                                client_state.get("graph_history") or []
                            ) + [
                                {"role": "user", "message": text_input, "clinical_extraction": False},
                                {"role": "agent", "message": _boundary_response, "clinical_extraction": False},
                            ]
                            await send_text_message(
                                websocket,
                                client_state,
                                _boundary_response,
                                await build_interview_state_async(client_state),
                                user_response=None,
                            )
                            continue

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
                        # Retained temporarily for compatibility, but disabled:
                        # policy now requires the deterministic boundary above,
                        # never an AI-generated medical or off-topic answer.
                        _off_topic_eligible = False

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
                            _busy_message = "Please wait for the previous response."
                            await websocket.send_text(json.dumps({"type": "error", "text": _busy_message}))
                            await run_blocking(
                                _record_chat_audit,
                                client_state,
                                role="system",
                                content=_busy_message,
                                message_type="error",
                                error_code="turn_already_processing",
                            )
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
                                        "phase": "complete" if _is_complete else "interviewing",
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
                                if _prom_session_complete:
                                    result_state = {**result_state, "phase": "complete"}
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
                                    _save_section = (
                                        "Completed" if result_state.get("phase") == "complete"
                                        else result_state.get("current_section", "")
                                    )
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
                                    _processing_error_message = "Unable to process that response. Please try again."
                                    await websocket.send_text(
                                        json.dumps({"type": "error", "text": _processing_error_message})
                                    )
                                    await run_blocking(
                                        _record_chat_audit,
                                        client_state,
                                        role="system",
                                        content=_processing_error_message,
                                        message_type="error",
                                        error_code=error_type(e),
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

                        # Transcribe through the configured STT service (Gemini with
                        # an independent Google Cloud Speech fallback).
                        try:
                            from app.audio.stt import transcribe_audio_bytes, is_hallucination, clean_transcript

                            t_stt_start = time.perf_counter()
                            transcription = await run_blocking(
                                transcribe_audio_bytes, audio_bytes
                            )
                            t_stt_ms = (time.perf_counter() - t_stt_start) * 1000
                            print(f"[audio] STT latency={t_stt_ms:.0f}ms transcript_chars={len(transcription)}")

                            if contains_internal_transcription_prompt(transcription):
                                _log.warning(
                                    "internal_prompt_content_rejected_before_transcription_delivery",
                                    subject=pseudonymous_id(client_state.get("user_id")),
                                )
                                await websocket.send_text(json.dumps({
                                    "type": "error",
                                    "text": "The transcription could not be processed safely. Please record it again or type your response.",
                                }))
                                continue

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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
