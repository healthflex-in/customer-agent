"""Interview progress payload sent with each socket message.

These builders read the startup HealthAgent. That is the same object they
read when they lived in server.py. The per-connection agent is separate.
"""

from typing import Optional

from app.forms.progress import (
    calculate_form_progress,
    calculate_section_completion_status,
)
from app.forms.prom import prom_scale_is_answered as _prom_scale_is_answered
from app.forms.repository import fetch_form_attachments, fetch_form_by_id
from app.runtime.blocking import run_blocking

_form_fallback_agent = None


def bind_form_fallback_agent(agent) -> None:
    """Record the startup agent the progress payload already falls back to."""

    global _form_fallback_agent
    _form_fallback_agent = agent


def form_fallback_agent():
    return _form_fallback_agent


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
            form_data_for_progress = form_fallback_agent().form
    else:
        # No form_id yet, use health_agent.form (will be empty for new forms)
        form_data_for_progress = form_fallback_agent().form
    
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
        _section = form_fallback_agent().current_section

    return {
        "section": _section,
        "progress": progress,
        "missing_fields": form_fallback_agent().missing_fields,
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
