"""
Handle one conversational turn from a text_input WebSocket message.

Key design decisions (see design.md):
- Uses astream(stream_mode=["messages","values"]) for real-time token streaming.
- Per-connection HealthAgent (ctx.agent) — NEVER a singleton — prevents cross-user
  data contamination.
- Brand/FAQ/assessment questions are intercepted BEFORE the graph so the interview
  state is fully preserved.
- Session lock (ctx.session_lock) serialises text + audio to prevent race conditions.
"""
import json
import asyncio
from typing import TYPE_CHECKING

from app.interview.sanitize import sanitize_patient_input
from app.interview.rate_limit import rate_limiter
from src.graph.nodes.extract import _answer_brand_question, _answer_medical_question

if TYPE_CHECKING:
    from app.ws.context import WSContext


# ── Brand / FAQ signals ──────────────────────────────────────────────────────
# IMPORTANT: Keep these SPECIFIC. Generic phrases like "the clinic" or "visit"
# match normal patient speech like "my friend told me to visit the clinic".
_BRAND_SIGNALS = [
    "about stance health", "what is stance health", "tell me about stance health",
    "what does stance health do", "how does stance health",
    "stance health team", "stance health location",
    "how many centers does", "how many clinics does",
    "what do you offer", "tell me more about stance",
    # Visiting — only QUESTION forms (not statements like "told me to visit")
    "how can i visit", "how do i visit", "how to visit stance",
    "where is stance", "directions to stance",
    "how to get to stance", "address of stance",
    "stance center location", "stance clinic location",
    "stance opening hours", "stance timings", "stance working hours",
    "stance contact", "stance phone",
    "book a session at stance", "schedule with stance", "appointment at stance",
    # Clinic/center location queries
    "other centers", "other clinics", "other branches",
    "where are the", "how many centers", "how many clinics",
    "nearest center", "nearest clinic", "center near",
    "tell me more about the clinic", "more about the clinic",
    "more about your clinic", "about the clinic",
    "about your center", "about the center",
]

# If message contains medical content, skip brand detection entirely
_MEDICAL_CONTENT_SIGNALS = [
    "pain", "hurt", "ache", "injury", "stiff", "tight", "swollen",
    "weak", "numb", "discomfort", "symptom", "issue", "problem",
    "months", "weeks", "days", "gradual", "sudden",
    "doctor", "physio", "hospital", "mri", "x-ray", "scan", "report",
    "smoke", "drink", "exercise", "surgery", "condition", "diabetes",
    "wrist", "knee", "back", "neck", "shoulder", "hip", "ankle", "elbow",
    "badminton", "cricket", "football", "running", "gym", "sport", "playing",
    "no pain", "no issues", "no problem", "getting worse", "getting better",
]

_EDU_SIGNALS = [
    "tell me more about", "can u tell me", "can you tell me",
    "what are the causes", "what causes", "what is the reason",
    "what are the symptoms", "how does", "explain",
    "tell me about", "more about", "difference between",
    "what happens", "why does",
]

_ASSESSMENT_SIGNALS = [
    "what do you think", "what do u think", "your opinion",
    "what would you say", "what's the situation", "what is the situation",
    "what could it be", "what might it be", "likely diagnosis",
    "your assessment", "does it sound like", "is it serious",
    "should i be worried", "how bad is it",
]


async def handle(ctx: "WSContext", text_input: str) -> None:
    """
    Execute one conversational turn:
    1. Sanitize + deduplicate + rate-limit
    2. Check for brand/edu/assessment off-topic questions → answer directly
    3. Acquire session lock → invoke graph via astream → stream tokens → send reply
    4. Persist form in background after reply
    """
    import time as _time
    ws = ctx.websocket
    cs = ctx.client_state

    # ── Sanitize ──────────────────────────────────────────────────────────────
    text_input = sanitize_patient_input(text_input)
    if not text_input:
        return

    # ── Deduplication — drop repeated identical messages within 8 seconds ────
    # The frontend sometimes sends the same audio transcription multiple times,
    # causing the same message to be processed 2-3 times in quick succession.
    _now = _time.time()
    _last_text = cs.get("_last_text_input", "")
    _last_time = cs.get("_last_text_time", 0.0)
    _text_sig = text_input[:120]  # compare first 120 chars to catch near-duplicates
    if _text_sig == _last_text[:120] and (_now - _last_time) < 8.0:
        print(f"[text_input] Duplicate message dropped (within 8s): {_text_sig[:40]!r}")
        return
    cs["_last_text_input"] = text_input
    cs["_last_text_time"] = _now

    # ── Rate limit ────────────────────────────────────────────────────────────
    uid = cs.get("user_id", ctx.client_id)
    if not rate_limiter.check(uid):
        await ws.send_text(json.dumps({
            "type": "error",
            "text": "Sending too fast — please wait a moment."
        }))
        return

    ti_lower = text_input.lower()

    # ── Off-topic: assessment request ────────────────────────────────────────
    # If the message contains medical/symptom content, skip brand/edu/assessment
    # detection entirely — this is a patient sharing health data, not asking about the clinic.
    has_medical_content = any(s in ti_lower for s in _MEDICAL_CONTENT_SIGNALS)

    is_assessment = not has_medical_content and any(s in ti_lower for s in _ASSESSMENT_SIGNALS)
    is_brand      = not has_medical_content and (
        any(s in ti_lower for s in _BRAND_SIGNALS) or (
            "stance" in ti_lower and len(ti_lower.split()) <= 6
            and "knee" not in ti_lower and "pain" not in ti_lower
        )
    )
    is_edu        = not has_medical_content and any(s in ti_lower for s in _EDU_SIGNALS)

    if is_assessment or is_brand or is_edu:
        answered = await _handle_offtopic(ctx, text_input, ti_lower,
                                          is_assessment, is_brand)
        if answered:
            return

    # ── Session lock: one turn at a time ─────────────────────────────────────
    if ctx.session_lock.locked():
        await ws.send_text(json.dumps({
            "type": "error",
            "text": "Still processing — please wait."
        }))
        return

    async with ctx.session_lock:
        await _run_graph_turn(ctx, text_input)


async def _handle_offtopic(ctx, text_input, ti_lower,
                            is_assessment, is_brand) -> bool:
    """Answer off-topic questions without touching the graph. Returns True if answered."""
    ws = ctx.websocket
    cs = ctx.client_state
    agent = ctx.agent

    ans = ""
    try:
        if is_assessment:
            form = cs.get("graph_form") or {}
            collected = [
                f"{k}: {v}"
                for sec in form.values() if isinstance(sec, dict)
                for k, v in sec.items() if v and str(v).strip()
            ]
            summary = "\n".join(collected) if collected else "No information collected yet."
            prompt = (
                f"You are Sage, a warm physiotherapy assistant at Stance Health.\n\n"
                f"Information collected:\n{summary}\n\n"
                f"Patient asked: \"{text_input}\"\n\n"
                f"Give a brief, empathetic preliminary impression (3-5 sentences). "
                f"Be honest but reassuring. End with: "
                f"'Your physiotherapist will do a full assessment to confirm this.'"
            )
            ans = await asyncio.to_thread(agent.llm_complete, prompt)

        elif is_brand:
            ans = await asyncio.to_thread(
                _answer_brand_question, text_input, agent.llm_complete
            )

        else:  # edu
            ans = await asyncio.to_thread(
                _answer_medical_question, text_input, agent.llm_complete
            )

        if not ans:
            prompt = (
                f"You are Sage, a warm physiotherapy assistant at Stance Health.\n"
                f"Patient asked: \"{text_input}\"\n"
                f"Answer helpfully in 3-5 sentences. Be warm and clear."
            )
            ans = await asyncio.to_thread(agent.llm_complete, prompt)
            ans = (ans or "").strip()

        if ans:
            # Stream tokens then send full message
            for word in (ans + " ").split(" "):
                if word:
                    await ws.send_text(json.dumps({"type": "token", "content": word + " "}))
            from app.interview.messaging import send_text_message
            from app.forms.progress import calculate_form_progress
            from src.graph.server_adapter import build_interview_state_from_graph
            await send_text_message(
                ws, cs, ans,
                _build_interview_state(cs),
                user_response=text_input,
            )
            return True
    except Exception as e:
        print(f"[text_input] Off-topic handler error: {e}")

    return False


async def _run_graph_turn(ctx: "WSContext", text_input: str) -> None:
    """Run one graph turn with astream for token streaming."""
    import asyncio
    ws = ctx.websocket
    cs = ctx.client_state
    agent = ctx.agent
    graph = ctx.graph

    from src.graph.server_adapter import build_graph_state, sync_client_state_from_graph
    from app.interview.messaging import send_text_message, _send_thought_update

    # ── Thought update: show card immediately ─────────────────────────────────
    current_sec = cs.get("graph_current_section", "")
    await ws.send_text(json.dumps({
        "type": "thought_update",
        "thoughts": [
            {"stage": "Reading your response",
             "detail": f"Processing: {text_input[:40]}...", "status": "active"},
            {"stage": "Extracting medical details",
             "detail": current_sec or "Present Complaint", "status": "pending"},
            {"stage": "Formulating next question", "detail": "", "status": "pending"},
        ]
    }))

    cs["_fetch_form_fn"] = ctx.fetch_form_fn
    agent._langfuse_user_id = cs.get("user_id", "")
    agent._langfuse_session_id = cs.get("session_id", "")

    graph_state = build_graph_state(cs, text_input, ctx.save_fn)

    # ── astream with token streaming ──────────────────────────────────────────
    result_state = None
    streamed_tokens = []
    try:
        async for chunk in graph.astream(
            graph_state,
            stream_mode=["messages", "values"],
            version="v2",
        ):
            if chunk["type"] == "messages":
                msg_chunk, meta = chunk["data"]
                token = getattr(msg_chunk, "content", "") or ""
                node = meta.get("langgraph_node", "")
                if token and node in (
                    "generate_question", "generate_summary",
                    "handle_summary_response", "apply_correction",
                    "handle_upload_response",
                ):
                    streamed_tokens.append(token)
                    await ws.send_text(json.dumps({"type": "token", "content": token}))
            elif chunk["type"] == "values":
                result_state = chunk["data"]
    except Exception as stream_err:
        print(f"[text_input] astream error: {stream_err} — falling back to invoke")
        result_state = await asyncio.get_event_loop().run_in_executor(
            None, graph.invoke, graph_state
        )

    sync_client_state_from_graph(cs, result_state)

    response_text = (
        result_state.get("response_text", "") if result_state
        else "".join(streamed_tokens)
    )
    phase = (result_state.get("phase") if result_state else None) or cs.get("graph_phase", "")
    print(f"[graph] Turn complete — phase: {phase}, response: {response_text[:80]}...")

    # ── Handle messages sent after interview is complete ─────────────────────
    # Graph routes phase="complete" directly to END → empty response_text.
    # If patient wants to add/correct something, reopen; otherwise give a nudge.
    if not response_text and phase == "complete":
        lower = text_input.lower()
        reopen_signals = [
            "issue", "problem", "pain", "hurt", "ache", "injury", "complaint",
            "wait", "actually", "forgot", "also", "one more", "add", "change",
            "correct", "wrong", "mistake", "update", "neck", "back", "knee",
            "shoulder", "hip", "wrist", "ankle", "elbow", "spine", "head",
            "something more", "more to share", "more to add", "need to add",
            "want to share", "want to add", "want to tell", "something else",
            "one more thing", "another thing", "more information", "more info",
            "share something", "tell you something", "mention something",
        ]
        # Health complaint signals — need to switch visit mode
        complaint_signals = [
            "pain", "hurt", "ache", "injury", "discomfort", "stiff", "swollen",
            "issue", "problem", "complaint", "neck", "back", "knee", "shoulder",
            "hip", "wrist", "ankle", "elbow", "spine", "head",
        ]
        if any(s in lower for s in reopen_signals):
            cs["graph_phase"] = "interviewing"

            # If they're revealing a health complaint, reset visit context and
            # clear "Not applicable" values so the conductor treats this as a
            # specific complaint visit — not a general one.
            if any(s in lower for s in complaint_signals):
                cs["graph_visit_context"] = "specific_complaint"
                _form = cs.get("graph_form") or {}
                _na_values = {
                    "not applicable", "general visit — no specific complaint",
                    "not applicable — no pain reported", "not applicable — general visit",
                    "not applicable — general visit.", "none",
                }
                for _sec in ["Present Complaint", "Pain Assessment", "Previous Consultations"]:
                    if _sec in _form and isinstance(_form[_sec], dict):
                        for _field in list(_form[_sec].keys()):
                            if str(_form[_sec].get(_field, "")).strip().lower() in _na_values:
                                _form[_sec][_field] = ""
                cs["graph_form"] = _form
                response_text = "Of course! Tell me about the neck pain — where exactly do you feel it, how severe is it on a scale of 0–10, and how long have you had it?"
            else:
                response_text = "Of course! Please go ahead — what would you like to share?"
        else:
            response_text = "Your information has been recorded. Feel free to let me know if you'd like to add or change anything!"

    # ── Progress ──────────────────────────────────────────────────────────────
    from app.forms.progress import calculate_form_progress
    cached_form = (result_state or {}).get("form", {})
    result_phase = (result_state or {}).get("phase", "") or phase
    if result_phase == "complete":
        progress = 100.0
    elif result_phase == "summary":
        # Interview is effectively done — show at least 85% regardless of empty fields.
        # Fields may be empty because the patient had nothing to report (general visit),
        # which is a valid completed state.
        progress = max(calculate_form_progress(cached_form), 85.0)
    else:
        progress = calculate_form_progress(cached_form)

    # ── Send reply ────────────────────────────────────────────────────────────
    await send_text_message(
        ws, cs, response_text,
        _build_interview_state(cs, result_state, progress),
        user_response=text_input,
        force_request_attachment=bool((result_state or {}).get("request_attachment")),
    )

    # ── TTS: stream audio response in background ──────────────────────────────
    if response_text:
        asyncio.create_task(_stream_tts(ws, response_text))

    # ── Background save ───────────────────────────────────────────────────────
    save_uid = cs.get("user_id", "")
    save_form_id = cs.get("form_id", "")
    save_section = (result_state or {}).get("current_section", "") or cs.get("graph_current_section", "")

    # Only save if there is actual form data (non-empty field values).
    # A fresh form template (all empty strings) is truthy but has no data —
    # saving it would overwrite a previously filled form with blank values.
    def _has_form_data(form: dict) -> bool:
        return any(
            v for sec in form.values() if isinstance(sec, dict)
            for v in sec.values() if v and str(v).strip()
        )

    _rs_form = (result_state or {}).get("form") or {}
    _cs_form = cs.get("graph_form") or {}

    if _has_form_data(_rs_form):
        save_form = _rs_form
    elif _has_form_data(_cs_form):
        save_form = _cs_form
    else:
        save_form = None  # Nothing worth saving — skip DB write

    if save_form:
        asyncio.create_task(asyncio.to_thread(
            ctx.save_fn, save_uid, save_form, save_section, save_form_id
        ))


async def _stream_tts(ws, text: str) -> None:
    """Generate TTS audio with edge-tts and stream to client. Runs as a background task."""
    try:
        from app.audio.tts import text_to_wav
        from app.audio.streaming import stream_audio_to_client
        import time
        wav = await text_to_wav(text)
        if wav:
            await stream_audio_to_client(ws, wav, f"tts_{int(time.time() * 1000)}")
    except Exception as e:
        print(f"[tts] background stream failed: {e}")


def _build_interview_state(cs: dict, result_state=None, progress: float = 0.0) -> dict:
    """Build the interview_state payload sent to the frontend."""
    from app.forms.progress import calculate_section_completion_status
    section = (result_state or {}).get("current_section") or cs.get("graph_current_section", "")
    form = (result_state or {}).get("form") or cs.get("graph_form") or {}
    sec_status = calculate_section_completion_status(form)
    return {
        "section": section,
        "current_section": section,
        "progress": progress,
        "missing_fields": (result_state or {}).get("missing_fields") or [],
        "attachments": [],
        "formId": cs.get("form_id", ""),
        "sectionProgress": sec_status,
    }
