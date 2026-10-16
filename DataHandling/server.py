from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
# faster_whisper removed — STT now uses Google Cloud Speech-to-Text (see app/audio/stt.py)
import time
import json
from dotenv import load_dotenv
load_dotenv()

# Import HealthAgent and related functionalities
from src.llm.functionalities import HealthAgent

# Centralized configuration (env vars, constants, paths).
# Aliased to the legacy names used throughout this file so handler code is
# unchanged. New code should import directly from app.config.
from app.config import (
    CORS_ALLOWED_ORIGINS,
    MAX_AUDIO_SESSION_BYTES,
    MAX_AUDIO_SESSION_SECONDS,
)
from app.ai.completer import build_reasoning_completer
from app.db.mongo import init_mongo, mongo
from app.audio.limits import audio_limit_error
from app.runtime.blocking import run_blocking
from app.jobs.worker import _report_worker_loop
from app.api.forms import register_form_routes
from app.api.health import register_health_routes
from app.api.users import register_user_routes
from app.forms.progress import calculate_form_progress
from app.forms.repository import save_customer_info, save_for_graph
from app.interview.session import new_client_state
from app.forms.questions import fetch_tagged_questions
from app.ws.completion import send_completed_form
from app.ws.handlers.control import (
    handle_audio_start,
    handle_end_session,
    handle_ping,
)
from app.ws.handlers.audio import handle_audio_end
from app.ws.handlers.interview import (
    handle_load_form,
    handle_start_interview,
    handle_start_new_form,
)
from app.ws.handlers.text import handle_text_input
from app.ws.messages import send_text_message
from app.ws.state import bind_form_fallback_agent, build_interview_state_async
from app.audit.chat_history import record_client_chat as _record_chat_audit
from app.ws.idempotency import RecentRequestWindow
from app.observability.privacy import error_type, pseudonymous_id
from app.observability.logging import log as _log
from app.observability.metrics import WS_CONNECTIONS, install_metrics
from app.security.rate_limit import RateLimiter
# Pure stateless helpers. Aliased to legacy names used throughout this file.

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
bind_form_fallback_agent(health_agent)
print("HealthAgent initialized")


# ── LangGraph interview graph ────────────────────────────────────────────────
# Build once at startup; each WebSocket turn calls graph.invoke(state).
_interview_graph = None
try:
    from src.graph.graph import build_interview_graph

    # In-memory checkpointer — MongoDB is the persistent source of truth.
    from src.graph.graph import create_checkpointer as _create_checkpointer
    _checkpointer = _create_checkpointer()

    # Form extraction uses its own completer so the interview model and the
    # extraction model can differ. The provider lives behind that function.
    _reasoning_llm_complete = build_reasoning_completer()

    _interview_graph = build_interview_graph(
        llm_complete=health_agent.llm_complete,
        system_prompt=health_agent.system_prompt,
        save_customer_info_fn=save_for_graph,
        checkpointer=_checkpointer,
        reasoning_llm=_reasoning_llm_complete,
    )
    print("[graph] LangGraph interview graph initialized")
except Exception as _graph_err:
    print(f"[graph] LangGraph initialization failed with {error_type(_graph_err)}; using fallback")
    _interview_graph = None




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
        form_data_copy, removed_prompt_values = scrub_internal_transcription_prompts(
            copy.deepcopy(form_data)
        )
        if removed_prompt_values:
            _log.warning(
                "internal_prompt_content_removed_before_persistence",
                removed_values=removed_prompt_values,
                subject=pseudonymous_id(user_id),
            )
        
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
        from app.uploads.attachments import attachment_urls
        customer_doc["attachments"] = attachment_urls(attachments_to_store)

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
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_form_list_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
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
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_latest_form_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
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
            form, removed_prompt_values = scrub_internal_transcription_prompts(form)
            if removed_prompt_values:
                _log.warning(
                    "internal_prompt_content_removed_during_form_read",
                    removed_values=removed_prompt_values,
                    subject=pseudonymous_id(user_id),
                )
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
        # Patient UI uses metadata records; MongoDB/dashboard stores URL strings.
        # Preserve the existing REST/WS shape while accepting both DB formats.
        from urllib.parse import urlparse, unquote
        return [
            item if isinstance(item, dict) else {
                "url": item, "type": "report", "label": "Medical Report",
                "fileName": unquote(urlparse(item).path.rsplit("/", 1)[-1]),
            }
            for item in (doc.get("attachments", []) if doc else [])
            if isinstance(item, (str, dict))
        ]
    except Exception as e:
        print(f"[fetch_attachments] Retrieval failed: {error_type(e)}")
        return []


def create_placeholder_form(
    user_id: str,
    client_state: dict,
    preferred_attempt_id: Optional[str] = None,
) -> Optional[str]:
    """Resolve or durably reserve the first intake attempt for a patient.

    A newly allocated identity is persisted as an empty ``draft`` immediately.
    Draft TTL cleanup prevents abandoned reservations from accumulating, while
    persistence guarantees that a browser refresh can safely resume the same
    attempt. Repeat intakes are still never allocated here; they must be
    assigned by the consultant workflow.
    """
    # Check if a form already exists — if so, reuse its exact attempt.
    existing = None if preferred_attempt_id else fetch_latest_form_for_user(user_id)
    if existing:
        existing_id = existing.get("formId", DEFAULT_FORM_ID)
        client_state["form_id"] = existing_id
        client_state["attempt_id"] = resolve_form_attempt_id(existing)
        print("[create_placeholder_form] Reusing existing form")
        return existing_id

    # No existing form: allocate (or recover) the first attempt and persist an
    # expiring draft so the identity remains valid across reloads.
    client_state["form_id"] = DEFAULT_FORM_ID
    client_state["attempt_id"] = (
        str(preferred_attempt_id).strip()
        if preferred_attempt_id
        else resolve_form_attempt_id(existing)
    )
    if not client_state["attempt_id"]:
        raise ValueError("Attempt ID cannot be empty")

    saved_form_id = save_customer_info(
        user_id=user_id,
        form_data={},
        current_section="Present Complaint",
        form_id=DEFAULT_FORM_ID,
        attempt_id=client_state["attempt_id"],
    )
    if not saved_form_id:
        raise RuntimeError("Unable to reserve the new assessment attempt")
    print("[create_placeholder_form] Persisted expiring draft attempt")
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


def _record_chat_audit(
    client_state: dict,
    *,
    role: str,
    content: str,
    message_type: str = "text_message",
    request_id: object = None,
    question_id: object = None,
    error_code: object = None,
    input_source: object = None,
    transcription_id: object = None,
    audio_capture_id: object = None,
    audio_filename: object = None,
    event_key: object = None,
    phase: object = None,
    section: object = None,
) -> bool:
    """Persist one short-lived transcript message without exposing it to logs."""

    return record_chat_message(
        chat_history_collection,
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
        input_source=input_source,
        transcription_id=transcription_id,
        audio_capture_id=audio_capture_id,
        audio_filename=audio_filename,
        event_key=event_key,
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
    audit_message_type: str = "text_message",
    audit_event_key: Optional[str] = None,
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
        message_type=audit_message_type,
        event_key=audit_event_key,
        question_id=(question_meta or {}).get("questionId"),
        phase=resolved_interview_state.get("status"),
        section=resolved_interview_state.get("section"),
    )


COMPLETED_FORM_MESSAGE = (
    "This assessment has already been completed and is now read-only. "
    "If another assessment is needed, your clinician will send you a new assessment link."
)


def build_completed_form_payload(client_state: dict, form: dict) -> dict:
    """Build the deterministic, zero-AI response for a terminal attempt."""

    form_id = form.get("formId") or client_state.get("form_id")
    attempt_id = form.get("attemptId") or client_state.get("attempt_id")
    return {
        "type": "form_completed",
        "text": COMPLETED_FORM_MESSAGE,
        "session_id": client_state["session_id"],
        "formId": form_id,
        "attemptId": attempt_id,
        "status": FORM_COMPLETED,
        "completedAt": _serialize_datetime(form.get("completedAt")),
        "interview_state": {
            "section": "Completed",
            "progress": 100.0,
            "missing_fields": [],
            "attachments": [],
            "formId": form_id,
            "attemptId": attempt_id,
            "sectionProgress": [],
            "promSteps": None,
            "status": FORM_COMPLETED,
            "locked": True,
        },
    }


async def send_completed_form(websocket: WebSocket, client_state: dict, form: dict) -> None:
    """Lock a completed attempt in-session and notify the patient without AI."""

    client_state["form_id"] = form.get("formId") or client_state.get("form_id")
    client_state["attempt_id"] = form.get("attemptId") or client_state.get("attempt_id")
    client_state["attempt_locked"] = True
    client_state["locked_form"] = {
        "formId": client_state.get("form_id"),
        "attemptId": client_state.get("attempt_id"),
        "status": FORM_COMPLETED,
        "completedAt": form.get("completedAt"),
    }
    client_state["is_recording"] = False
    client_state["received_audio_buffer"].clear()
    client_state["recording_start_time"] = None
    client_state["pending_voice_input"] = None
    await websocket.send_text(json.dumps(build_completed_form_payload(client_state, form)))
    await run_blocking(
        _record_chat_audit,
        client_state,
        role="agent",
        content=COMPLETED_FORM_MESSAGE,
        message_type="form_completed",
        phase=FORM_COMPLETED,
        section="Completed",
    )


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
    client_state["pending_voice_input"] = None
    client_state["last_missing_field_set"] = None
    client_state["last_question_field_ids"] = None
    client_state["same_missing_field_count"] = 0
    client_state["same_question_count"] = 0
    client_state["prom_snapshot"] = None
    client_state["prom_source_doc_id"] = None

    print(f"[reset_health_agent] HealthAgent reset complete. Form is now empty: {len(agent.form)} sections")








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

    client_state = new_client_state()
    _session_processing = {"active": False}  # simple flag to prevent concurrent processing
    print(
        f"WebSocket session started with interview {client_state['interview_id']} "
        "(user will be set on start_interview)"
    )

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
                        await handle_start_interview(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            _interview_graph,
                            fetch_tagged_questions=fetch_tagged_questions,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                        )

                    elif msg_type == "start_new_form":
                        await handle_start_new_form(websocket)

                    elif msg_type == "load_form":
                        await handle_load_form(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                        )

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
                        if await handle_text_input(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            _interview_graph,
                            _session_processing=_session_processing,
                            _save_for_graph=save_for_graph,
                            build_interview_state_async=build_interview_state_async,
                            send_text_message=send_text_message,
                            _record_chat_audit=_record_chat_audit,
                            _request_window=_request_window,
                            _rate_limiter=_rate_limiter,
                        ):
                            break

                    elif msg_type == "audio_end":
                        await handle_audio_end(websocket, data, client_state)

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