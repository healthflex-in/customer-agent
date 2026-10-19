"""WebSocket commands that open or resume an interview.

The connection loop in server.py accepts the socket and decides which command
may run. These handlers perform that command.
"""

import json

from fastapi import WebSocket

from app.clinical.scope import assess_msk_intake_scope
from app.config import DEFAULT_FORM_ID
from app.db.mongo import mongo
from app.db.ownership import build_owned_form_filter
from app.db.queries import collection_namespace
from app.db.users import validate_user_exists
from app.forms.instruments import build_prom_snapshot, questions_from_prom_snapshot
from app.forms.lifecycle import has_meaningful_form_data, is_completed_form
from app.forms.progress import calculate_form_progress
from app.forms.prom import (
    PROM_SCALE_MAP as _PROM_SCALE_MAP,
    prom_scale_is_answered as _prom_scale_is_answered,
)
from app.forms.questions import batch_tagged_questions, personalize_questions
from app.forms.repository import (
    create_placeholder_form,
    fetch_form_by_id,
    save_customer_info,
)
from app.interview.session import reset_health_agent_for_new_interview
from app.observability.logging import log as _log
from app.observability.privacy import error_type, pseudonymous_id
from app.runtime.blocking import run_blocking
from app.ws.completion import send_completed_form
from src.graph.server_adapter import init_graph_state_in_client


async def handle_start_interview(
    websocket: WebSocket,
    data: dict,
    client_state: dict,
    health_agent,
    _interview_graph,
    *,
    fetch_tagged_questions,
    send_text_message,
    build_interview_state_async,
) -> None:
    """Start or resume the interview for the patient on this socket."""
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
        return
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
        return

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
        return

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
        return

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
            return
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
            return

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
            return

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
                return

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
                return
            return
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
        return
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
        return
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


async def handle_start_new_form(websocket: WebSocket) -> None:
    """Refuse a patient-created assessment. A clinician assigns the attempt."""
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
    return


async def handle_load_form(
    websocket: WebSocket,
    data: dict,
    client_state: dict,
    health_agent,
    *,
    send_text_message,
    build_interview_state_async,
) -> None:
    """Load a saved form onto this socket."""
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
        return

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
        return
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
        return

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
        return

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
        return

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

