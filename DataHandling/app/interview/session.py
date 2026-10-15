"""Per-connection interview session reset."""


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
