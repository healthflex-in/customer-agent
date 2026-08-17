"""
server_adapter.py — Bridge between server.py WebSocket state (client_state dict)
and the LangGraph InterviewState TypedDict.

These helpers are intentionally pure / side-effect-free (except where
explicitly documented), so they are easy to unit-test independently of the
WebSocket machinery.
"""
from __future__ import annotations

from typing import Callable, Optional

from src.graph.state import InterviewState, get_fresh_interview_state


# ---------------------------------------------------------------------------
# Phase mapping helpers
# ---------------------------------------------------------------------------

_TALK_MODE_TO_PHASE: dict[str, str] = {
    "START": "welcome",
    "USER": "interviewing",
}

_PHASE_TO_TALK_MODE: dict[str, str] = {
    "welcome": "START",
    "interviewing": "USER",
    "awaiting_upload": "USER",
    "summary": "USER",
    "complete": "USER",
}


def _talk_mode_to_phase(talk_mode: str) -> str:
    return _TALK_MODE_TO_PHASE.get(talk_mode, "interviewing")


# ---------------------------------------------------------------------------
# 1. build_graph_state
# ---------------------------------------------------------------------------

def build_graph_state(
    client_state: dict,
    user_input: str,
    save_customer_info_fn: Callable,
) -> InterviewState:
    """
    Build a complete InterviewState from the WebSocket client_state dict and the
    current user turn's text input.

    The graph-specific keys that server.py stores are prefixed with ``graph_``:
        graph_form, graph_question_round, graph_current_section,
        graph_phase, graph_history, graph_form_sections

    Falls back to sensible defaults (via get_fresh_interview_state) for any key
    that is absent or None, so this is safe to call even before
    init_graph_state_in_client() has been called.

    Args:
        client_state:          The per-connection state dict maintained by server.py.
        user_input:            The transcribed / typed text from the user this turn.
        save_customer_info_fn: Reference to server.py's save_customer_info function
                               (not called here, kept for signature symmetry so callers
                               can pass it through to graph nodes if needed).

    Returns:
        A fully populated InterviewState.
    """
    user_id = client_state.get("user_id") or ""
    form_id = client_state.get("form_id") or ""
    session_id = client_state.get("session_id") or ""

    # Retrieve graph-specific fields; fall back to fresh-state defaults when absent.
    fresh = get_fresh_interview_state(user_id=user_id, form_id=form_id, session_id=session_id)

    # For PROM forms (tagged questions), use the PROM template as the initial form
    _prom_template = client_state.get("tagged_form_template")
    _graph_form = client_state.get("graph_form")
    if _prom_template and not _graph_form:
        # First turn in a PROM session — start with the PROM template
        import copy as _copy
        form = _copy.deepcopy(_prom_template)
    else:
        form = _graph_form or fresh["form"]
    question_round = client_state.get("graph_question_round")
    if question_round is None:
        question_round = fresh["question_round"]

    current_section = client_state.get("graph_current_section") or fresh["current_section"]
    phase = client_state.get("graph_phase") or fresh["phase"]
    history = client_state.get("graph_history") or fresh["history"]
    form_sections = client_state.get("graph_form_sections") or fresh["form_sections"]

    # Check if REST upload already saved attachments → mark as uploaded so graph
    # doesn't keep prompting for uploads. Must be computed before InterviewState().
    _reports_uploaded = client_state.get("graph_reports_uploaded", False)
    if not _reports_uploaded and client_state.get("graph_awaiting_report_upload", False):
        try:
            _fetch = client_state.get("_fetch_form_fn")
            if _fetch:
                _doc = _fetch(client_state.get("form_id", ""), client_state.get("user_id", ""))
                if _doc and _doc.get("attachments"):
                    _reports_uploaded = True
                    client_state["graph_reports_uploaded"] = True
        except Exception:
            pass

    return InterviewState(
        # Identity
        user_id=user_id,
        form_id=form_id,
        session_id=session_id,
        # Turn input
        user_input=user_input,
        # Form data
        form=form,
        # Navigation
        question_round=question_round,
        current_section=current_section,
        form_sections=form_sections,
        # Phase
        phase=phase,
        # Conversation flags
        asked_previous_consultations=client_state.get("graph_asked_previous_consultations", False),
        reports_uploaded=_reports_uploaded,
        awaiting_report_upload=client_state.get("graph_awaiting_report_upload", False),
        attempts_on_current_section=client_state.get("graph_attempts_on_current_section", 0),
        referral_asked=client_state.get("graph_referral_asked", False),
        visit_context=client_state.get("graph_visit_context", "unknown"),
        # History
        history=history,
        # Intra-turn scratch fields — always reset at the start of each turn
        missing_fields=[],
        summary_intent=None,
        reports_intent=None,
        is_correction_turn=False,
        correction_data=None,
        # Orchestrator
        orchestrator_question_id=client_state.get("current_question_id"),
        orchestrator_question_text=None,
        # Tagged-questions turns (persist across turns)
        tagged_turns=client_state.get("tagged_turns"),
        tagged_turn_metas=client_state.get("tagged_turn_metas"),
        tagged_turn_index=client_state.get("tagged_turn_index", 0),
        tagged_cleanup_done=client_state.get("tagged_cleanup_done", False),
        tagged_form_template=client_state.get("tagged_form_template"),
        tagged_question_meta=None,  # always reset; populated by generate node
        # Output — always reset at turn start; populated by graph nodes
        response_text="",
        request_attachment=False,
    )


# ---------------------------------------------------------------------------
# 2. sync_client_state_from_graph
# ---------------------------------------------------------------------------

def sync_client_state_from_graph(
    client_state: dict,
    result_state: InterviewState,
) -> None:
    """
    Write the fields that must survive across WebSocket turns back into
    client_state after the graph has finished processing a turn.

    Only the fields that are owned by the graph (navigation, form data, phase,
    history) are synced. Transient intra-turn scratch fields are intentionally
    not persisted.

    Args:
        client_state:  The per-connection WebSocket state dict (mutated in place).
        result_state:  The InterviewState returned by the graph after the turn.
    """
    client_state["graph_form"] = result_state["form"]
    client_state["graph_question_round"] = result_state["question_round"]
    client_state["graph_current_section"] = result_state["current_section"]
    client_state["graph_phase"] = result_state["phase"]
    client_state["graph_history"] = result_state["history"]

    # Persist the extra flags so they survive into the next turn.
    client_state["graph_asked_previous_consultations"] = result_state.get(
        "asked_previous_consultations", False
    )
    client_state["graph_reports_uploaded"] = result_state.get("reports_uploaded", False)
    client_state["graph_awaiting_report_upload"] = result_state.get(
        "awaiting_report_upload", False
    )
    client_state["graph_attempts_on_current_section"] = result_state.get(
        "attempts_on_current_section", 0
    )
    client_state["graph_referral_asked"] = result_state.get("referral_asked", False)
    visit_context = result_state.get("visit_context", "unknown")
    if visit_context and visit_context != "unknown":
        client_state["graph_visit_context"] = visit_context
    # Sync tagged turn index and cleanup flag so the next turn picks up where we left off
    client_state["tagged_turn_index"] = result_state.get("tagged_turn_index", 0)
    client_state["tagged_cleanup_done"] = result_state.get("tagged_cleanup_done", False)


# ---------------------------------------------------------------------------
# 3. build_interview_state_from_graph
# ---------------------------------------------------------------------------

def build_interview_state_from_graph(
    result_state: InterviewState,
    client_state: dict,
    fetch_form_attachments_fn: Optional[Callable] = None,
    calculate_form_progress_fn: Optional[Callable] = None,
    calculate_section_completion_status_fn: Optional[Callable] = None,
    fetch_form_by_id_fn: Optional[Callable] = None,
    progress_override: Optional[float] = None,
) -> dict:
    """
    Build the ``interview_state`` payload that server.py sends to the frontend
    inside every ``text_message`` WebSocket frame.

    Replicates the structure produced by server.py's ``build_interview_state()``
    so the frontend receives an identical schema whether the call originates from
    the legacy HealthAgent path or the new graph path.

    The returned dict has the shape::

        {
            "section": str,
            "progress": float,          # 0-100
            "missing_fields": list,
            "attachments": list,
            "formId": str | None,
            "sectionProgress": dict,    # output of calculate_section_completion_status
        }

    Args:
        result_state:
            The InterviewState produced by the graph for this turn.
        client_state:
            The per-connection WebSocket state dict (used to read user_id / form_id).
        fetch_form_attachments_fn:
            Optional reference to server.py's ``fetch_form_attachments(form_id, user_id)``.
            When provided, real attachment data is included. Defaults to returning [].
        calculate_form_progress_fn:
            Optional reference to server.py's ``calculate_form_progress(form_data)``.
            When provided, field-level progress is computed. Defaults to 0.
        calculate_section_completion_status_fn:
            Optional reference to server.py's
            ``calculate_section_completion_status(form_data)``. Defaults to {}.
        fetch_form_by_id_fn:
            Optional reference to server.py's ``fetch_form_by_id(form_id, user_id)``.
            When provided, database form data is used for progress calculation
            (more accurate after uploads), matching the behaviour of the legacy
            ``build_interview_state(use_db_form=True)`` path.
        progress_override:
            If supplied, used directly as the progress value (0-100).

    Returns:
        dict matching the frontend-expected ``interview_state`` schema.
    """
    form_id = client_state.get("form_id")
    user_id = client_state.get("user_id")

    # ── Attachments ──────────────────────────────────────────────────────────
    attachments: list = []
    if fetch_form_attachments_fn and form_id and user_id:
        try:
            attachments = fetch_form_attachments_fn(form_id, user_id) or []
        except Exception as exc:
            print(f"[build_interview_state_from_graph] Warning: could not fetch attachments: {exc}")

    # ── Form data for progress (prefer DB when available) ───────────────────
    form_data_for_progress = result_state["form"]
    if fetch_form_by_id_fn and form_id and user_id:
        try:
            db_form = fetch_form_by_id_fn(form_id, user_id)
            if db_form and db_form.get("form_data"):
                form_data_for_progress = db_form["form_data"]
        except Exception as exc:
            print(f"[build_interview_state_from_graph] Warning: could not fetch DB form: {exc}")

    # ── Progress ─────────────────────────────────────────────────────────────
    if progress_override is not None:
        progress = progress_override
    elif calculate_form_progress_fn:
        try:
            progress = calculate_form_progress_fn(form_data_for_progress)
        except Exception as exc:
            print(f"[build_interview_state_from_graph] Warning: progress calculation failed: {exc}")
            progress = 0.0
    else:
        progress = 0.0

    # ── Section completion status ─────────────────────────────────────────────
    section_progress: dict = {}
    if calculate_section_completion_status_fn:
        try:
            section_progress = calculate_section_completion_status_fn(form_data_for_progress)
        except Exception as exc:
            print(f"[build_interview_state_from_graph] Warning: section status calculation failed: {exc}")

    # PROM session detection: template (remaining) OR previously-answered data
    _tagged_tmpl = client_state.get("tagged_form_template")
    _prev_prom   = client_state.get("prom_existing_data") or {}
    _is_prom     = bool(_tagged_tmpl) or bool(_prev_prom)
    _all_prom_scales = list({**_prev_prom, **(_tagged_tmpl or {})}.keys()) if _is_prom else []

    # Section label
    if _is_prom:
        _remaining = list(_tagged_tmpl.keys()) if _tagged_tmpl else []
        if _remaining:
            _pt_idx = max(0, client_state.get("tagged_turn_index", 1) - 1)
            _pt_idx = min(_pt_idx, len(_remaining) - 1)
            section = _remaining[_pt_idx]
        else:
            section = _all_prom_scales[-1] if _all_prom_scales else "Outcome Assessment"
    else:
        section = result_state["current_section"]

    # For PROM, recompute progress from all scales (template has only the remaining ones)
    if _is_prom and progress_override is None:
        def _answered(v) -> bool:
            if isinstance(v, dict):
                return any(str(x).strip() for x in v.values() if x)
            return bool(v and str(v).strip())
        _prom_fd = form_data_for_progress or {}
        _ans = sum(1 for k in _all_prom_scales if _answered(_prom_fd.get(k)) or _answered(_prev_prom.get(k)))
        progress = round(_ans / len(_all_prom_scales) * 100) if _all_prom_scales else 0

    return {
        "section": section,
        "progress": progress,
        "missing_fields": result_state.get("missing_fields") or [],
        "attachments": attachments,
        "formId": form_id,
        "sectionProgress": section_progress,
        "promSteps": _all_prom_scales if _is_prom else None,
    }


# ---------------------------------------------------------------------------
# 4. init_graph_state_in_client
# ---------------------------------------------------------------------------

def init_graph_state_in_client(
    client_state: dict,
    user_id: str,
    form_id: str,
    form: Optional[dict] = None,
) -> None:
    """
    Initialize (or reset) all ``graph_*`` keys in client_state for a new interview.

    Call this when starting a fresh interview so that ``build_graph_state`` has
    well-defined initial values to read.  For a resume flow, load the saved form
    data first and pass it as ``form``; all other navigation state will be
    recomputed by the graph on the first turn.

    Args:
        client_state: The per-connection WebSocket state dict (mutated in place).
        user_id:      The authenticated application user ID.
        form_id:      The form ID (typically DEFAULT_FORM_ID from app.config).
        form:         Optional pre-loaded form dict. When None a blank form template
                      is used (equivalent to a brand-new interview).
    """
    fresh = get_fresh_interview_state(
        user_id=user_id,
        form_id=form_id,
        session_id=client_state.get("session_id", ""),
    )

    client_state["user_id"] = user_id
    client_state["form_id"] = form_id

    # Form data: supplied form > PROM template (if tagged questions) > standard template
    if form is not None:
        client_state["graph_form"] = form
    elif client_state.get("tagged_form_template"):
        import copy as _copy
        client_state["graph_form"] = _copy.deepcopy(client_state["tagged_form_template"])
    else:
        client_state["graph_form"] = fresh["form"]

    # Navigation
    client_state["graph_question_round"] = fresh["question_round"]
    client_state["graph_current_section"] = fresh["current_section"]
    client_state["graph_form_sections"] = fresh["form_sections"]

    # Phase
    client_state["graph_phase"] = fresh["phase"]

    # History
    client_state["graph_history"] = list(fresh["history"])

    # Conversation flags
    client_state["graph_asked_previous_consultations"] = False
    client_state["graph_reports_uploaded"] = False
    client_state["graph_awaiting_report_upload"] = False
    client_state["graph_attempts_on_current_section"] = 0
    client_state["graph_referral_asked"] = False
    client_state["graph_visit_context"] = "unknown"
    # Tagged turns — set by server.py after fetching from MongoDB; preserve if already set
    if "tagged_turns" not in client_state:
        client_state["tagged_turns"] = None
    if "tagged_turn_metas" not in client_state:
        client_state["tagged_turn_metas"] = None
    if "tagged_turn_index" not in client_state:
        client_state["tagged_turn_index"] = 0
    if "tagged_form_template" not in client_state:
        client_state["tagged_form_template"] = None
    client_state["tagged_cleanup_done"] = False
