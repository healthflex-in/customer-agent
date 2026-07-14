"""
Handle the start_interview WebSocket message.

Flow:
1. Validate userId from payload.
2. Validate user exists in MongoDB.
3. Check if user already has a form → RESUME MODE vs NEW INTERVIEW MODE.
4. Resume: load form, sync graph state, send welcome-back + next question.
5. New: reset agent, create form slot, send welcome + first question.
"""
from __future__ import annotations

import asyncio
import json
import time as _time_module
from typing import TYPE_CHECKING

# ── Server-level deduplication (persists across WebSocket reconnects) ─────────
# client_state resets on every new WS connection, so per-connection deduplication
# breaks on reconnects. This module-level dict survives reconnects.
_start_interview_cooldowns: dict[str, float] = {}  # user_id -> last_start_time
_COOLDOWN_SECONDS = 86400  # 24 hours — data persists until form is explicitly completed

from bson import ObjectId

from app.config import DEFAULT_FORM_ID
from app.db.mongo import get_users_collection
from app.forms.repository import (
    create_placeholder_form,
    fetch_form_by_id,
    fetch_latest_form_for_user,
)
from app.interview.messaging import send_text_message

if TYPE_CHECKING:
    from app.ws.context import WSContext


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_user_exists(user_id: str) -> bool:
    """Return True if *user_id* exists in the users collection."""
    collection = get_users_collection()
    if collection is None:
        print("[start_interview] MongoDB not connected — cannot validate user")
        return False
    try:
        oid = ObjectId(user_id) if isinstance(user_id, str) else user_id
    except Exception:
        print(f"[start_interview] Invalid user_id format: {user_id}")
        return False
    return collection.find_one({"_id": oid}) is not None


def _build_interview_state(cs: dict, form: dict | None = None, progress: float = 0.0) -> dict:
    """Build the interview_state payload sent to the frontend."""
    from app.forms.progress import calculate_form_progress, calculate_section_completion_status
    effective_form = form or cs.get("graph_form") or {}
    if progress == 0.0 and effective_form:
        try:
            progress = calculate_form_progress(effective_form)
        except Exception:
            progress = 0.0
    sec_status = {}
    try:
        sec_status = calculate_section_completion_status(effective_form)
    except Exception:
        pass
    return {
        "section": cs.get("graph_current_section", "Present Complaint"),
        "current_section": cs.get("graph_current_section", "Present Complaint"),
        "progress": progress,
        "missing_fields": [],
        "attachments": [],
        "formId": cs.get("form_id", ""),
        "sectionProgress": sec_status,
    }


def _count_filled_fields(form_data: dict) -> int:
    count = 0
    for section_fields in form_data.values():
        if isinstance(section_fields, dict):
            for value in section_fields.values():
                if value and str(value).strip():
                    count += 1
    return count


def _reset_agent_for_new_interview(agent, user_id: str, cs: dict) -> None:
    """Reset per-connection HealthAgent for a brand-new interview."""
    agent.init_form()
    agent.talk_mode = "START"
    agent.history = []
    agent.history.append({"role": "agent", "message": agent.welcome_prompt})
    cs["user_id"] = user_id
    cs["form_id"] = None
    print(f"[start_interview] HealthAgent reset for new interview, user: {user_id}")


# ── Main handler ─────────────────────────────────────────────────────────────

async def handle(ctx: "WSContext", data: dict) -> None:
    """Entry point called by the WS dispatcher for start_interview messages."""
    ws = ctx.websocket
    cs = ctx.client_state
    agent = ctx.agent

    # ── Deduplication — ignore rapid duplicate start_interview events ─────────
    # The frontend sometimes sends start_interview multiple times on reconnect,
    # causing a duplicate welcome message. Guard with a 10-second cooldown.
    # Use server-level cooldown (persists across WebSocket reconnects).
    # 30-minute window covers any realistic session; prevents mid-session restarts
    # from browser reconnects. Only bypass if the previous session was fully completed.
    user_id_check = data.get("userId", "")
    _now = _time_module.time()
    if user_id_check:
        _last = _start_interview_cooldowns.get(user_id_check, 0.0)
        _elapsed = _now - _last
        if _elapsed < _COOLDOWN_SECONDS:
            # Allow restart ONLY if the form was completed (all fields filled / phase=complete).
            # While the interview is in progress, any reconnect just resumes — no restart.
            _allow_restart = False
            try:
                _existing = fetch_latest_form_for_user(user_id_check)
                if _existing:
                    _form_data = _existing.get("form_data", {})
                    _all_filled = all(
                        v and str(v).strip()
                        for sec in _form_data.values() if isinstance(sec, dict)
                        for v in sec.values()
                    )
                    if _all_filled or _existing.get("phase") == "complete":
                        _allow_restart = True
                        print(f"[start_interview] Form complete — allowing new session for {user_id_check}")
            except Exception:
                pass
            if not _allow_restart:
                print(f"[start_interview] Form in progress — reconnect restart blocked for {user_id_check}")
                return
        _start_interview_cooldowns[user_id_check] = _now

    # ── 1. Validate userId ────────────────────────────────────────────────────
    user_id = data.get("userId")
    if not user_id:
        await ws.send_text(json.dumps({
            "type": "error",
            "message": "Missing userId in start_interview. Please log in and retry.",
        }))
        return

    # ── 2. Validate user exists in MongoDB ────────────────────────────────────
    if not _validate_user_exists(user_id):
        collection = get_users_collection()
        db_name = collection.database.name if collection else "unknown"
        coll_name = collection.name if collection else "users"
        await ws.send_text(json.dumps({
            "type": "error",
            "message": (
                f"User {user_id} does not exist in {db_name}.{coll_name}. "
                "Please use a valid user ID from your user management system."
            ),
        }))
        return

    # ── 3. Set core client_state identifiers ─────────────────────────────────
    cs["user_id"] = user_id
    cs["form_id"] = DEFAULT_FORM_ID

    # ── 4. Init LangGraph state ───────────────────────────────────────────────
    if ctx.graph is not None:
        try:
            from src.graph.server_adapter import init_graph_state_in_client
            init_graph_state_in_client(cs, user_id=user_id, form_id=DEFAULT_FORM_ID)
            cs["graph_phase"] = "welcome"
            print(f"[start_interview] Graph state initialised for user: {user_id}")
        except Exception as ge:
            print(f"[start_interview] init_graph_state_in_client failed (non-fatal): {ge}")

    # ── 5. Check for existing form → decide mode ──────────────────────────────
    existing_form = fetch_latest_form_for_user(user_id)
    is_resuming = existing_form is not None

    if not is_resuming:
        print(f"[start_interview] NEW INTERVIEW MODE: no existing form for user {user_id}")
        _reset_agent_for_new_interview(agent, user_id, cs)

    # ── 6a. RESUME MODE ───────────────────────────────────────────────────────
    if is_resuming:
        print(f"[start_interview] RESUME MODE: loading existing form for user {user_id}")
        loaded = fetch_form_by_id(DEFAULT_FORM_ID, user_id)

        if not loaded:
            # Unexpected: latest form lookup succeeded but fetch_by_id returned nothing.
            # Fall through to new-interview mode.
            print(f"[start_interview] fetch_form_by_id returned None — falling back to new interview")
            is_resuming = False
            _reset_agent_for_new_interview(agent, user_id, cs)
        else:
            # Security: verify ownership (belt-and-suspenders).
            form_owner = loaded.get("userId")
            if str(form_owner) != str(user_id):
                print(f"[start_interview] SECURITY: form belongs to {form_owner}, not {user_id}")
                await ws.send_text(json.dumps({
                    "type": "error",
                    "message": "Access denied: this form belongs to a different user.",
                }))
                return

            form_data = loaded.get("form_data", {})
            current_section = loaded.get("current_section", "Present Complaint")

            # Validate sections to derive where to resume.
            try:
                from src.graph.pure_functions.form_validation import validate_section as _vs
                pc_complete    = not bool(_vs(form_data, "Present Complaint"))
                prev_complete  = not bool(_vs(form_data, "Previous Consultations"))
                pain_complete  = not bool(_vs(form_data, "Pain Assessment"))
                hist_complete  = not bool(_vs(form_data, "History & Diagnostics"))
                goals_complete = not bool(_vs(form_data, "Treatment Goals"))
                ref_complete   = not bool(_vs(form_data, "Referral"))
            except Exception as ve:
                print(f"[start_interview] Section validation error (non-fatal): {ve}")
                pc_complete = prev_complete = pain_complete = False
                hist_complete = goals_complete = ref_complete = False

            def _first_incomplete() -> str:
                if not pc_complete:    return "Present Complaint"
                if not prev_complete:  return "Previous Consultations"
                if not pain_complete:  return "Pain Assessment"
                if not hist_complete:  return "History & Diagnostics"
                if not goals_complete: return "Treatment Goals"
                if not ref_complete:   return "Referral"
                return current_section

            if not pc_complete or not prev_complete:
                resume_idx = 0
            elif not pain_complete or not hist_complete:
                resume_idx = 1
            elif not goals_complete or not ref_complete:
                resume_idx = 2
            else:
                resume_idx = 3

            resume_section = _first_incomplete()

            # Sync agent (needed for generate_summary / make_template calls).
            agent.form = form_data
            agent.current_section = resume_section
            agent.idx = resume_idx
            agent.talk_mode = "USER"

            # Sync graph state from loaded form data.
            cs["graph_phase"] = "interviewing"
            cs["graph_form"] = form_data
            cs["graph_current_section"] = resume_section
            cs["graph_question_round"] = resume_idx if resume_idx < 3 else 2
            try:
                from src.prompts import get_medical_form_template as _get_tmpl
                cs["graph_form_sections"] = list(_get_tmpl().keys())
            except Exception:
                pass

            print(
                f"[start_interview] Loaded form {DEFAULT_FORM_ID}, "
                f"section: {resume_section}, idx: {resume_idx}"
            )

            # Generate summary if enough data.
            filled = _count_filled_fields(form_data)
            summary_text: str | None = None
            MIN_FIELDS_FOR_SUMMARY = 5

            if filled >= MIN_FIELDS_FOR_SUMMARY:
                try:
                    summary_text = await asyncio.to_thread(agent.generate_summary)
                except Exception as se:
                    print(f"[start_interview] Summary generation error (non-fatal): {se}")

            if summary_text:
                resume_msg = (
                    "Welcome back! Here's a quick summary of what I've noted so far:\n\n"
                    f"{summary_text}\n\n"
                    "Let's continue from where we left off."
                )
            else:
                resume_msg = (
                    "Welcome back. We haven't collected much information yet — "
                    "let's continue right away."
                )

            interview_state = _build_interview_state(cs, form_data)
            await send_text_message(ws, cs, resume_msg, interview_state, user_response=None)

            # Don't auto-fire a next question — the graph will generate it
            # intelligently when the user sends their first message.
            return  # RESUME path complete

    # ── 6b. NEW INTERVIEW MODE ────────────────────────────────────────────────
    # Set phase to "welcome" so that when the user sends their first message,
    # the LangGraph routes to handle_first_turn_node — the LLM conductor that
    # reads their opening message and generates the appropriate first question.
    # Do NOT auto-fire Q1 here; wait for the user to speak first.
    cs["graph_phase"] = "welcome"

    # Assign a form slot (no DB write until first answer is saved).
    create_placeholder_form(user_id, cs)
    print(f"[start_interview] New interview started for user: {user_id}, form: {cs.get('form_id')}")

    from src.prompts import WELCOME_PROMPT
    interview_state = _build_interview_state(cs, progress=0.0)

    # Send only the welcome message — let the user respond first.
    await send_text_message(ws, cs, WELCOME_PROMPT.strip(), interview_state, user_response=None)
