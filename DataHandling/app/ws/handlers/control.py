"""Connection-control messages: heartbeat, recording start, and session end.

These handlers do not run an interview turn. The connection loop in
server.py decides when they are allowed to run.
"""

import copy
import json
import time

from fastapi import WebSocket

from app.observability.privacy import error_type


async def handle_ping(websocket: WebSocket) -> None:
    """Reply to an application heartbeat.

    Browser WebSockets cannot emit protocol-level ping frames. This keeps
    idle patient sessions alive through proxies and load balancers with a
    one-minute idle timeout. It never changes interview state.
    """
    await websocket.send_text(json.dumps({"type": "pong"}))


def handle_audio_start(client_state: dict) -> None:
    """Mark this socket as receiving audio.

    The recording clock is monotonic on the server. Client timestamps can be
    absent, use different units, or be manipulated.
    """
    client_state["is_recording"] = True
    client_state["received_audio_buffer"] = bytearray()
    client_state["recording_start_time"] = time.monotonic()
    print("Client started sending audio")


async def handle_end_session(
    websocket: WebSocket,
    data: dict,
    client_state: dict,
    health_agent,
    *,
    save_customer_info,
    send_text_message,
    build_interview_state_async,
    calculate_form_progress,
    run_blocking,
) -> None:
    """Persist the current interview and tell the client the summary was saved."""
    provided_user_id = data.get("userId")
    if provided_user_id:
        client_state["user_id"] = provided_user_id

    try:
        health_agent.save_progress()
        form_id = client_state.get("form_id")
        await run_blocking(
            save_customer_info,
            user_id=client_state["user_id"],
            form_data=copy.deepcopy(health_agent.form),
            current_section=health_agent.current_section,
            form_id=form_id,
            attempt_id=client_state.get("attempt_id"),
        )
        await send_text_message(
            websocket,
            client_state,
            "Interview summary saved.",
            await build_interview_state_async(
                client_state,
                progress_override=calculate_form_progress(
                    health_agent.form
                ),
            ),
            user_response=None,
        )
    except Exception as e:
        print(f"[end_session] Save failed: {error_type(e)}")
