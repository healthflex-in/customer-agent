"""WebSocket endpoint — thin dispatcher. All logic lives in stages/."""
import json
import asyncio
import time
from fastapi import WebSocket, WebSocketDisconnect

from app.ws.context import WSContext
from app.ws.protocol import Inbound
from app.db.mongo import get_users_collection


async def websocket_endpoint(websocket: WebSocket, client_id: str, graph, save_fn, fetch_form_fn, fetch_attachments_fn, fetch_latest_form_fn) -> None:
    from app.ws._shutting_down_flag import is_shutting_down
    if is_shutting_down():
        await websocket.close(code=1001, reason="Server restarting")
        return

    await websocket.accept()
    from app.ws._active_connections import active_connections, ws_connections_gauge
    active_connections.add(websocket)
    ws_connections_gauge.inc()

    # Per-connection HealthAgent — prevents cross-user data contamination
    from src.llm.functionalities import HealthAgent
    agent = HealthAgent()

    client_state = {
        "session_id": f"session_{int(time.time())}",
        "user_id": None, "form_id": None,
        "interview_id": f"interview_{int(time.time() * 1000) % 10**10}",
        "is_recording": False,
        "received_audio_buffer": b"",
        "graph_form": None, "graph_question_round": None,
        "graph_current_section": None, "graph_phase": None,
        "graph_form_sections": None, "graph_history": None,
        "graph_asked_previous_consultations": False,
        "graph_reports_uploaded": False,
        "graph_awaiting_report_upload": False,
        "graph_attempts_on_current_section": 0,
        "orchestrator_session_id": None,
        "current_question_id": None,
        "_fetch_form_fn": fetch_form_fn,
    }
    session_lock = asyncio.Lock()

    ctx = WSContext(
        websocket=websocket,
        client_id=client_id,
        client_state=client_state,
        agent=agent,
        graph=graph,
        session_lock=session_lock,
        save_fn=save_fn,
        fetch_form_fn=fetch_form_fn,
        fetch_attachments_fn=fetch_attachments_fn,
        fetch_latest_form_fn=fetch_latest_form_fn,
        send_thought_fn=lambda *a, **kw: None,  # placeholder
    )

    print(f"Client {client_id} connected")
    try:
        while True:
            raw = await websocket.receive()
            if "text" not in raw:
                continue
            try:
                data = json.loads(raw["text"])
            except json.JSONDecodeError:
                continue
            msg_type = data.get("type", "")
            await _dispatch(ctx, msg_type, data)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        err = str(e)
        if "1001" not in err and "1000" not in err and "disconnect" not in err.lower():
            print(f"[ws] Unexpected error for {client_id}: {e}")
    finally:
        active_connections.discard(websocket)
        ws_connections_gauge.dec()
        print(f"Client {client_id} disconnected")


async def _dispatch(ctx: WSContext, msg_type: str, data: dict) -> None:
    ws = ctx.websocket
    try:
        if msg_type == Inbound.TEXT_INPUT:
            text = data.get("text", "").strip()
            if text:
                from app.ws.stages.text_input import handle as handle_text
                await handle_text(ctx, text)

        elif msg_type == Inbound.START_INTERVIEW:
            from app.ws.stages.start_interview import handle as handle_start
            await handle_start(ctx, data)

        elif msg_type == Inbound.AUDIO_START:
            ctx.client_state["is_recording"] = True
            ctx.client_state["received_audio_buffer"] = b""

        elif msg_type == Inbound.AUDIO_CHUNK:
            chunk_data = data.get("data", b"")
            if isinstance(chunk_data, str):
                import base64
                chunk_data = base64.b64decode(chunk_data)
            ctx.client_state["received_audio_buffer"] += chunk_data

        elif msg_type == Inbound.AUDIO_END:
            from app.ws.stages.audio_pipeline import handle as handle_audio
            await handle_audio(ctx, data)

        elif msg_type == Inbound.END_SESSION:
            print(f"[ws] Session ended for {ctx.client_id}")

    except Exception as e:
        err = str(e)
        is_disconnect = any(c in err for c in ("1001","1000","going away","ConnectionClosed","disconnect"))
        if not is_disconnect:
            print(f"[ws] Stage error ({msg_type}): {e}")
            try:
                import json
                await ws.send_text(json.dumps({"type": "error", "text": f"Error: {err[:100]}"}))
            except Exception:
                pass
