"""Per-connection interview session state."""

import uuid


def new_client_state() -> dict:
    """Return the empty state for one newly accepted socket."""

    client_state = {
        "session_id": f"session_{uuid.uuid4()}",
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
        # Server-owned link between the last delivered transcription and
        # the patient's next submitted answer (voice vs typed audit).
        "pending_voice_input": None,
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
        "graph_question_ask_counts": {},
        "graph_skip_reframe_ids": [],
        "graph_skip_reframed_ids": [],
        # Repeat-question telemetry (see turn_tracking.update_loop_counters)
        "last_missing_field_set": None,
        "last_question_field_ids": None,
        "same_missing_field_count": 0,
        "same_question_count": 0,
    }
    # Create a new interview record
    # MongoDB DISABLED - Using placeholder values instead
    # interview_id = db.create_interview(user_id, client_state["session_id"])
    interview_id = f"interview_{uuid.uuid4()}"  # Connection-scoped trace identity
    client_state["interview_id"] = interview_id
    return client_state


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
    client_state["pending_voice_input"] = None
    client_state["graph_question_ask_counts"] = {}
    client_state["graph_skip_reframe_ids"] = []
    client_state["graph_skip_reframed_ids"] = []
    client_state["last_missing_field_set"] = None
    client_state["last_question_field_ids"] = None
    client_state["same_missing_field_count"] = 0
    client_state["same_question_count"] = 0

    print(f"[reset_health_agent] HealthAgent reset complete. Form is now empty: {len(agent.form)} sections")
