"""WebSocket text answers for an interview already in progress.

The connection loop in server.py accepts the socket and decides which command
may run. This handler performs a typed answer.
"""

import json
import time

from fastapi import WebSocket

from app.audit.chat_history import resolve_patient_input_provenance
from app.clinical.escalation import assess_urgent_risk, record_escalation
from app.clinical.scope import assess_msk_intake_scope
from app.config import DEFAULT_FORM_ID
from app.content_safety import contains_internal_transcription_prompt
from app.db.mongo import mongo
from app.forms.instruments import apply_prom_answers
from app.forms.lifecycle import COMPLETED as FORM_COMPLETED, is_completed_form
from app.forms.progress import (
    calculate_form_progress,
    calculate_section_completion_status,
)
from app.forms.prom import (
    PROM_SCALE_MAP as _PROM_SCALE_MAP,
    advance_tagged_turn,
    parse_structured_prom_answers,
    prom_field_label as _prom_field_label,
    prom_scale_is_answered as _prom_scale_is_answered,
)
from app.forms.repository import (
    fetch_form_attachments,
    fetch_form_by_id,
    save_customer_info,
)
from app.observability.logging import log as _log
from app.observability.privacy import error_type, pseudonymous_id
from app.observability.tracing import (
    langfuse_client as _langfuse,
    langfuse_enabled as _langfuse_enabled,
)
from app.runtime.blocking import run_blocking
from app.security.input import sanitize_patient_input
from app.ws.completion import send_completed_form
from app.ws.idempotency import RequestDecision
from app.ws.thoughts import send_thought_update as _send_thought_update
from src.graph.pure_functions.clarification import (
    boundary_response_with_pending_question,
    build_out_of_flow_response,
    has_actionable_intake_fact,
    pending_intake_question,
)
from src.graph.server_adapter import (
    build_graph_state,
    build_interview_state_from_graph,
    sync_client_state_from_graph,
)


async def handle_text_input(
    websocket: WebSocket,
    data: dict,
    client_state: dict,
    health_agent,
    _interview_graph,
    *,
    _session_processing: dict,
    _save_for_graph,
    build_interview_state_async,
    send_text_message,
    _record_chat_audit,
    _request_window,
    _rate_limiter,
) -> bool:
    """Apply one typed patient answer.

    Returns true when this connection must close, which is the clinical
    escalation path. Every other answer stays on the open socket.
    """
    # Client is sending text input (manual or auto-sent transcription)
    text_input = data.get("text", "").strip()
    if not text_input:
        return
    if contains_internal_transcription_prompt(text_input):
        _log.warning(
            "internal_prompt_content_rejected_from_text_input",
            subject=pseudonymous_id(client_state.get("user_id")),
        )
        await websocket.send_text(json.dumps({
            "type": "error",
            "text": "The transcription could not be processed safely. Please record it again or type your response.",
        }))
        return

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
        return
    if _request_decision is RequestDecision.CONFLICT:
        await websocket.send_text(json.dumps({
            "type": "error",
            "text": "requestId was already used for a different question",
        }))
        return
    if _request_decision is RequestDecision.DUPLICATE:
        await websocket.send_text(json.dumps({
            "type": "submission_ack",
            "status": "duplicate",
            "requestId": data.get("requestId"),
        }))
        return

    text_input = sanitize_patient_input(text_input)
    if not _rate_limiter.check(client_state.get("user_id", "anon")):
        await websocket.send_text(json.dumps({"type": "error", "text": "Too many messages. Please slow down."}))
        return

    print(f"[text_input] Received patient input ({len(text_input)} chars)")
    print(f"[text_input] Current talk_mode: {health_agent.talk_mode}, history length: {len(health_agent.history)}")
    _input_provenance = resolve_patient_input_provenance(
        content=text_input,
        payload=data,
        pending_voice_input=client_state.get("pending_voice_input"),
    )
    # A delivered transcript belongs to at most one submitted
    # message. Clear it even when the patient discarded it and
    # typed something else, preventing stale file attribution.
    client_state["pending_voice_input"] = None
    _is_voice_input = (
        _input_provenance.get("inputSource")
        == "voice_transcription"
    )
    await run_blocking(
        _record_chat_audit,
        client_state,
        role="patient",
        content=text_input,
        message_type=("voice_input" if _is_voice_input else "text_input"),
        request_id=data.get("requestId"),
        question_id=data.get("questionId"),
        input_source=_input_provenance.get("inputSource"),
        transcription_id=_input_provenance.get("transcriptionId"),
        audio_capture_id=_input_provenance.get("audioCaptureId"),
        audio_filename=_input_provenance.get("audioFilename"),
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
        return True

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
        return

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
    # A mixed turn may contain both a valid intake fact and
    # a request for advice (for example, a severity update
    # followed by a medication question). Let the graph save
    # only the fact and return the same safety boundary.
    if _boundary_response and has_actionable_intake_fact(text_input):
        _boundary_response = None
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
        return

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
                return
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
                return
        except Exception as _bre:
            print(f"[off_topic] Error: {_bre} — falling through to graph")

    if _session_processing["active"]:
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
        return
    _session_processing["active"] = True
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
                    _prom_answer_saved = await run_blocking(
                        save_customer_info,
                        user_id=client_state.get("user_id", ""),
                        form_data=_prom_injected_form,
                        current_section="PROM",
                        form_id=client_state.get("form_id", ""),
                        preferred_doc_id=client_state.get("prom_source_doc_id"),
                        attempt_id=client_state.get("attempt_id"),
                        prom_snapshot=client_state.get("prom_snapshot"),
                    )
                    if not _prom_answer_saved:
                        raise RuntimeError("PROM answer persistence failed")

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
                if _is_complete:
                    _prom_completed_id = await run_blocking(
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
                    if not _prom_completed_id:
                        raise RuntimeError("PROM completion persistence failed")

                client_state["tagged_turn_index"] = _new_index
                _history = list(client_state.get("graph_history") or [])
                _history.extend([
                    {"role": "user", "message": text_input},
                    {"role": "agent", "message": response_text},
                ])
                client_state["graph_history"] = _history
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
                return

            client_state["_fetch_form_fn"] = fetch_form_by_id
            # Refresh structured data from the exact persisted
            # attempt before every graph turn. This makes MongoDB
            # the source of truth after reconnects/concurrent stale
            # sockets and prevents next-question generation from
            # validating an older in-memory form.
            _latest_attempt = await run_blocking(
                fetch_form_by_id,
                client_state.get("form_id", ""),
                client_state.get("user_id", ""),
                client_state.get("attempt_id"),
            )
            if is_completed_form(_latest_attempt):
                await send_completed_form(
                    websocket, client_state, _latest_attempt
                )
                return
            if _latest_attempt and isinstance(
                _latest_attempt.get("form_data"), dict
            ):
                import copy as _turn_copy
                _latest_form_data = _turn_copy.deepcopy(
                    _latest_attempt["form_data"]
                )
                if client_state.get("form_id") == DEFAULT_FORM_ID:
                    from src.forms.loader import load_form as _turn_load_form
                    _hydrated = _turn_load_form("FRM-01").empty_form()
                    for _section, _fields in _latest_form_data.items():
                        if _section in _hydrated and isinstance(_fields, dict):
                            _hydrated[_section].update(_fields)
                        elif _section.startswith("Additional Complaint ") and isinstance(_fields, dict):
                            _hydrated[_section] = _fields
                    _latest_form_data = _hydrated
                client_state["graph_form"] = _latest_form_data
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
            from src.graph.pure_functions.turn_tracking import (
                current_complaint_index,
                missing_field_ids,
                update_loop_counters,
                updated_field_ids,
            )
            _phase_before = graph_state.get("phase", "unknown")
            _section_before = graph_state.get("current_section", "")
            _missing_before = missing_field_ids(
                graph_state.get("form", {}),
                graph_state.get("form_sections", []),
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
                            # Buffer tokens until persistence succeeds.
                            # Sending an update/confirmation token before
                            # MongoDB accepts the mutation can tell a patient
                            # that unsaved information was recorded.
                    elif chunk["type"] == "values":
                        result_state = chunk["data"]  # accumulate final state
            except Exception as _stream_err:
                print(f"[graph] Streaming failed with {error_type(_stream_err)}; using invoke")
                result_state = await run_blocking(
                    _interview_graph.invoke,
                    graph_state,
                )

            _result_form = result_state.get("form", {})
            _result_sections = result_state.get(
                "form_sections"
            ) or list(_result_form)
            _updated_fields = updated_field_ids(
                graph_state.get("form", {}), _result_form
            )
            _missing_after = missing_field_ids(
                _result_form, _result_sections
            )
            _question_field_ids = list(
                result_state.get("question_field_ids") or []
            )
            (
                _same_missing_count,
                _same_question_count,
                _loop_recovery,
            ) = update_loop_counters(
                client_state,
                missing_ids=_missing_after,
                question_ids=_question_field_ids,
            )

            if _loop_recovery and not _updated_fields and _question_field_ids:
                # Re-read the attempt and narrow the question to one
                # verified missing field. This cannot fabricate or
                # complete data; it only prevents an identical batch
                # from looping while surfacing the stuck field in logs.
                _recovery_form = await run_blocking(
                    fetch_form_by_id,
                    client_state.get("form_id", ""),
                    client_state.get("user_id", ""),
                    client_state.get("attempt_id"),
                )
                _recovery_question_ids = list(_question_field_ids)
                if _recovery_form and isinstance(
                    _recovery_form.get("form_data"), dict
                ):
                    import copy as _recovery_copy
                    _persisted_data = _recovery_copy.deepcopy(
                        _recovery_form["form_data"]
                    )
                    if client_state.get("form_id") == DEFAULT_FORM_ID:
                        from src.forms.loader import load_form as _recovery_load
                        _recovery_hydrated = _recovery_load("FRM-01").empty_form()
                        for _section, _fields in _persisted_data.items():
                            if _section in _recovery_hydrated and isinstance(_fields, dict):
                                _recovery_hydrated[_section].update(_fields)
                            elif _section.startswith("Additional Complaint ") and isinstance(_fields, dict):
                                _recovery_hydrated[_section] = _fields
                        _persisted_data = _recovery_hydrated
                    _persisted_missing = missing_field_ids(
                        _persisted_data, list(_persisted_data)
                    )
                    result_state["form"] = _persisted_data
                    result_state["form_sections"] = list(_persisted_data)
                    _result_form = _persisted_data
                    _result_sections = list(_persisted_data)
                    _missing_after = _persisted_missing
                    _recovery_question_ids = [
                        field_id for field_id in _question_field_ids
                        if field_id in _persisted_missing
                    ] or list(_persisted_missing)
                    _log.warning(
                        "intake_loop_recovery",
                        attempt_id=client_state.get("attempt_id"),
                        session_id=client_state.get("session_id"),
                        missing_fields=_persisted_missing,
                        question_field_ids=_question_field_ids,
                        same_question_count=_same_question_count,
                        same_missing_field_count=_same_missing_count,
                    )
                if _recovery_question_ids:
                    from src.graph.pure_functions.question_plan import (
                        question_for_missing_fields,
                    )
                    from src.forms.loader import load_form as _loop_form
                    _first_id = _recovery_question_ids[0]
                    _first_section, _first_field = _first_id.split(".", 1)
                    _focused = question_for_missing_fields(
                        [(_first_section, _first_field)],
                        _loop_form("FRM-01").field_labels,
                    )
                    result_state["response_text"] = (
                        "I’m sorry this information still appears missing. "
                        "Let’s verify one detail at a time.\n\n" + _focused
                    )
                    result_state["phase"] = "interviewing"
                    result_state["current_section"] = _first_section
                    result_state["missing_fields"] = [_first_field]
                    result_state["question_field_ids"] = [_first_id]
                    _question_field_ids = [_first_id]
                else:
                    # The database now has every required answer.
                    # Return to review; never auto-complete without
                    # the patient's explicit confirmation.
                    from src.graph.pure_functions.summary import _fallback_summary
                    result_state["response_text"] = _fallback_summary(
                        result_state.get("form", {})
                    )
                    result_state["phase"] = "summary"
                    result_state["missing_fields"] = []
                    result_state["question_field_ids"] = []
                    _question_field_ids = []
                # The graph may have generated tokens for the
                # pre-recovery question. Never release those
                # after replacing the response from DB state.
                streamed_tokens = []
                if result_state.get("history"):
                    result_state["history"][-1] = {
                        "role": "agent",
                        "message": result_state["response_text"],
                    }

            _summary_intent = result_state.get("summary_intent") or {}
            _detected_intents = [
                _summary_intent.get("intent")
                or ("reports" if result_state.get("reports_intent") else "answer")
            ]
            _log.info(
                "intake_turn_state",
                attempt_id=client_state.get("attempt_id"),
                session_id=client_state.get("session_id"),
                phase_before=_phase_before,
                phase_after=result_state.get("phase", "unknown"),
                section_before=_section_before,
                section_after=result_state.get("current_section", ""),
                detected_intents=_detected_intents,
                extracted_fields=_updated_fields,
                updated_fields=_updated_fields,
                missing_fields_before=_missing_before,
                missing_fields_after=_missing_after,
                current_complaint_index=current_complaint_index(
                    result_state.get("current_section", "")
                ),
                question_field_ids=_question_field_ids,
                same_question_count=_same_question_count,
                same_missing_field_count=_same_missing_count,
                completion_eligible=(
                    not _missing_after
                    and result_state.get("phase") in {"summary", "complete"}
                ),
            )

            print(f"[timing] turn_total={(time.perf_counter() - _turn_t0) * 1000:.0f}ms "
                  f"phase={result_state.get('phase') if result_state else 'unknown'}")

            if _lf_ctx:
                try:
                    _lf_ctx.__exit__(None, None, None)
                except Exception:
                    pass

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
            _prom_persisted_tagged_index = None
            if _prom_next_turn_idx is not None:
                _tt_list = client_state.get("tagged_turns") or []
                _tt_metas = client_state.get("tagged_turn_metas") or []
                if _prom_next_turn_idx < len(_tt_list):
                    response_text = _tt_list[_prom_next_turn_idx]
                    _prom_override_meta = (
                        _tt_metas[_prom_next_turn_idx]
                        if _prom_next_turn_idx < len(_tt_metas) else None
                    )
                    _prom_persisted_tagged_index = _prom_next_turn_idx + 1
                    print(f"[prom] override turn {_prom_next_turn_idx}, next_idx={_prom_next_turn_idx + 1}")
                else:
                    response_text = "Thank you for completing the assessment! Your responses have been recorded."
                    _prom_persisted_tagged_index = _prom_next_turn_idx
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

            # Persist the mutation before claiming success to the
            # patient or calculating UI state from MongoDB. The old
            # send-then-save order could display an update that was
            # lost when the socket disconnected between those steps.
            _save_user_id = client_state.get("user_id", "")
            _save_form_id = client_state.get("form_id", "")
            _save_form = result_state.get("form", {})
            # For PROM forms: answers were already saved immediately after injection
            # above (in per-question format). Skip the post-graph collapse-save to
            # avoid overwriting the per-question dict structure with empty strings
            # if the graph didn't preserve the injected answers in result_state["form"].
            if client_state.get("tagged_form_template"):
                if _prom_session_complete:
                    _persisted_form_id = await run_blocking(
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
                    if not _persisted_form_id:
                        raise RuntimeError("PROM completion persistence failed")
            else:
                _save_section = (
                    "Completed" if result_state.get("phase") == "complete"
                    else result_state.get("current_section", "")
                )
                _persisted_form_id = await run_blocking(
                    _save_for_graph,
                    _save_user_id, _save_form, _save_section, _save_form_id,
                    None, None,
                    FORM_COMPLETED if result_state.get("phase") == "complete" else None,
                    client_state.get("attempt_id"),
                )
                if not _persisted_form_id:
                    raise RuntimeError("Intake turn persistence failed")

            # Advance in-memory navigation only after MongoDB has
            # accepted the form mutation. If persistence fails,
            # the patient receives an error and the previous
            # socket state remains authoritative for a retry.
            sync_client_state_from_graph(client_state, result_state)
            if _prom_persisted_tagged_index is not None:
                client_state["tagged_turn_index"] = _prom_persisted_tagged_index

            # Preserve the existing token protocol, but release
            # buffered tokens only after the clinical state is durable.
            if not client_state.get("tagged_form_template"):
                for _token in streamed_tokens:
                    await websocket.send_text(json.dumps({
                        "type": "token",
                        "content": _token,
                    }))

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
                            return
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
        _session_processing["active"] = False

