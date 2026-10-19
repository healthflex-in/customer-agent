from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
# faster_whisper removed — STT now uses Google Cloud Speech-to-Text (see app/audio/stt.py)
import time
import json
from dotenv import load_dotenv
load_dotenv()

# Import HealthAgent and related functionalities
from src.llm.functionalities import HealthAgent

# Centralized configuration (env vars, constants, paths).
# Aliased to the legacy names used throughout this file so handler code is
# unchanged. New code should import directly from app.config.
from app.config import (
    CORS_ALLOWED_ORIGINS,
    MAX_AUDIO_SESSION_BYTES,
    MAX_AUDIO_SESSION_SECONDS,
)
from app.ai.completer import build_reasoning_completer
from app.db.mongo import init_mongo, mongo
from app.audio.limits import audio_limit_error
from app.runtime.blocking import run_blocking
from app.jobs.worker import _report_worker_loop
from app.api.forms import register_form_routes
from app.api.health import register_health_routes
from app.api.users import register_user_routes
from app.forms.progress import calculate_form_progress
from app.forms.repository import save_customer_info, save_for_graph
from app.interview.session import new_client_state
from app.forms.questions import fetch_tagged_questions
from app.ws.completion import send_completed_form
from app.ws.handlers.control import (
    handle_audio_start,
    handle_end_session,
    handle_ping,
)
from app.ws.handlers.audio import handle_audio_end
from app.ws.handlers.interview import (
    handle_load_form,
    handle_start_interview,
    handle_start_new_form,
)
from app.ws.handlers.text import handle_text_input
from app.ws.messages import send_text_message
from app.ws.state import bind_form_fallback_agent, build_interview_state_async
from app.audit.chat_history import record_client_chat as _record_chat_audit
from app.ws.idempotency import RecentRequestWindow
from app.observability.privacy import error_type, pseudonymous_id
from app.observability.logging import log as _log
from app.observability.metrics import WS_CONNECTIONS, install_metrics
from app.security.rate_limit import RateLimiter
# Pure stateless helpers. Aliased to legacy names used throughout this file.

# Import MongoDB database connector
# MongoDB DISABLED - Commented out
# from db import MedicalInterviewDB

_rate_limiter = RateLimiter(max_per_minute=30)
_request_window = RecentRequestWindow(capacity=4096)

# ── Active connections tracker for graceful shutdown ─────────────────────────
_active_ws_connections: set = set()
_shutting_down = False
_report_worker_task: asyncio.Task | None = None
_report_worker_stop: asyncio.Event | None = None

# ── FastAPI app with lifespan for graceful startup/shutdown ──────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app_instance):
    global _shutting_down, _report_worker_task, _report_worker_stop
    _log.info("server_startup", service="healthflex-agent")
    _shutting_down = False
    _report_worker_stop = asyncio.Event()
    if mongo.report_jobs_collection is not None:
        _report_worker_task = asyncio.create_task(
            _report_worker_loop(_report_worker_stop)
        )
    yield
    # ── Graceful shutdown ───────────────────────────────────────────────────
    _shutting_down = True
    if _report_worker_stop is not None:
        _report_worker_stop.set()
    _log.info("server_shutdown_initiated", active_sessions=len(_active_ws_connections))
    # Notify active WebSocket sessions
    for ws in list(_active_ws_connections):
        try:
            await ws.send_text(json.dumps({
                "type": "system",
                "message": "Server restarting — your progress is saved. Reconnect in a few seconds."
            }))
        except Exception:
            pass
    # Wait up to 15s for sessions to wind down
    for _ in range(15):
        if not _active_ws_connections:
            break
        await asyncio.sleep(1)
    if _report_worker_task is not None:
        try:
            await asyncio.wait_for(_report_worker_task, timeout=15)
        except asyncio.TimeoutError:
            _report_worker_task.cancel()
            await asyncio.gather(_report_worker_task, return_exceptions=True)
        _report_worker_task = None
    _log.info("server_shutdown_complete")

app = FastAPI(lifespan=lifespan, title="Healthflex Customer Agent", version="2.0.0")

install_metrics(app)

# ── Agent thought stream: human-readable labels for each graph node ──────────


# Add CORS middleware to allow frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# STT now uses Google Cloud Speech-to-Text — no local model to load at startup.

# Initialize HealthAgent (kept for audio, summary title generation, and legacy fallback)
print("Initializing HealthAgent...")
health_agent = HealthAgent()
bind_form_fallback_agent(health_agent)
print("HealthAgent initialized")


# ── LangGraph interview graph ────────────────────────────────────────────────
# Build once at startup; each WebSocket turn calls graph.invoke(state).
_interview_graph = None
try:
    from src.graph.graph import build_interview_graph

    # In-memory checkpointer — MongoDB is the persistent source of truth.
    from src.graph.graph import create_checkpointer as _create_checkpointer
    _checkpointer = _create_checkpointer()

    # Form extraction uses its own completer so the interview model and the
    # extraction model can differ. The provider lives behind that function.
    _reasoning_llm_complete = build_reasoning_completer()

    _interview_graph = build_interview_graph(
        llm_complete=health_agent.llm_complete,
        system_prompt=health_agent.system_prompt,
        save_customer_info_fn=save_for_graph,
        checkpointer=_checkpointer,
        reasoning_llm=_reasoning_llm_complete,
    )
    print("[graph] LangGraph interview graph initialized")
except Exception as _graph_err:
    print(f"[graph] LangGraph initialization failed with {error_type(_graph_err)}; using fallback")
    _interview_graph = None




# _serialize_datetime and serialize_user moved to app.db.serializers (imported above).










# Initialize database
# MongoDB DISABLED - Commented out
# print("Connecting to MongoDB...")
# db = MedicalInterviewDB()
# print("MongoDB connected")
init_mongo()


register_health_routes(app, lambda: _interview_graph is not None)
register_user_routes(app)
register_form_routes(app)


# def modify_system_prompt():
#     """
#     Modify the system prompt to make the agent more strict about not making assumptions.
#     This modifies the HealthAgent's system prompt directly.
#     """
#     original_prompt = health_agent.system_prompt

#     # Add instructions to avoid making assumptions
#     assumption_instructions = """IMPORTANT: Stick to the question format as much as possible. They are presets."""

#     # Combine with original prompt
#     health_agent.system_prompt = original_prompt + assumption_instructions
#     print("Modified system prompt to reduce assumptions")


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    if _shutting_down:
        await websocket.close(code=1001, reason="Server restarting — reconnect shortly")
        return

    await websocket.accept()
    _active_ws_connections.add(websocket)
    WS_CONNECTIONS.inc()
    _log.info("ws_connected", subject=pseudonymous_id(client_id))
    print("WebSocket client connected")

    # Per-session processing lock — prevents text+audio race condition.
    # Only one message is processed at a time per session.
    _session_lock = asyncio.Lock()

    # ── Per-connection HealthAgent ───────────────────────────────────────────
    # CRITICAL: Each WebSocket connection gets its OWN HealthAgent instance.
    # The global health_agent singleton caused data contamination between
    # concurrent users — one user's form data would overwrite another's.
    # HealthAgent init is lightweight (just LLM + prompts, no ChromaDB).
    health_agent = await run_blocking(HealthAgent)

    client_state = new_client_state()
    _session_processing = {"active": False}  # simple flag to prevent concurrent processing
    print(
        f"WebSocket session started with interview {client_state['interview_id']} "
        "(user will be set on start_interview)"
    )

    # Initialize conversation history
    if not hasattr(health_agent, "history"):
        health_agent.history = []

    # # Make the agent more strict about not making assumptions
    # modify_system_prompt()

    # Don't send welcome message immediately - wait for client to send "start_interview" message
    # This prevents audio from playing before user logs in

    # Main WebSocket loop
    try:
        while True:
            # Receive message from client
            message = await websocket.receive()

            # Client closed the connection. Starlette delivers a single
            # {"type": "websocket.disconnect"} message and raises a RuntimeError
            # ("Cannot call receive once a disconnect message has been received")
            # if receive() is called again. Break cleanly instead of looping back
            # into receive() and crashing the handler.
            if message.get("type") == "websocket.disconnect":
                break

            # Handle text messages (JSON control messages)
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "")

                    if msg_type == "ping":
                        await handle_ping(websocket)
                        continue

                    # A completed attempt remains read-only even if an outdated
                    # or modified client tries to send input after receiving the
                    # lock response. Starting/loading a clinician-assigned attempt
                    # remains possible on the same socket.
                    if (
                        client_state.get("attempt_locked")
                        and msg_type not in {"start_interview", "load_form"}
                    ):
                        await send_completed_form(
                            websocket,
                            client_state,
                            client_state.get("locked_form") or {},
                        )
                        continue

                    if msg_type == "start_interview":
                        await handle_start_interview(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            _interview_graph,
                            fetch_tagged_questions=fetch_tagged_questions,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                        )

                    elif msg_type == "start_new_form":
                        await handle_start_new_form(websocket)

                    elif msg_type == "load_form":
                        await handle_load_form(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                        )

                    elif msg_type == "end_session":
                        await handle_end_session(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            save_customer_info=save_customer_info,
                            send_text_message=send_text_message,
                            build_interview_state_async=build_interview_state_async,
                            calculate_form_progress=calculate_form_progress,
                            run_blocking=run_blocking,
                        )

                    elif msg_type == "audio_start":
                        handle_audio_start(client_state)

                    elif msg_type == "text_input":
                        if await handle_text_input(
                            websocket,
                            data,
                            client_state,
                            health_agent,
                            _interview_graph,
                            _session_processing=_session_processing,
                            _save_for_graph=save_for_graph,
                            build_interview_state_async=build_interview_state_async,
                            send_text_message=send_text_message,
                            _record_chat_audit=_record_chat_audit,
                            _request_window=_request_window,
                            _rate_limiter=_rate_limiter,
                        ):
                            break

                    elif msg_type == "audio_end":
                        await handle_audio_end(websocket, data, client_state)

                except json.JSONDecodeError:
                    print("[websocket] Invalid JSON message received")
                except Exception as e:
                    print(f"[websocket] Text-message handling failed: {error_type(e)}")

            # Handle binary messages (audio data)
            elif "bytes" in message:
                if client_state["is_recording"]:
                    # Accumulate audio chunks
                    audio_chunk = message["bytes"]
                    started_at = client_state.get("recording_start_time")
                    elapsed_seconds = (
                        max(0.0, time.monotonic() - started_at)
                        if isinstance(started_at, (int, float))
                        else 0.0
                    )
                    limit_error = audio_limit_error(
                        current_bytes=len(client_state["received_audio_buffer"]),
                        incoming_bytes=len(audio_chunk),
                        elapsed_seconds=elapsed_seconds,
                        max_total_bytes=MAX_AUDIO_SESSION_BYTES,
                        max_duration_seconds=MAX_AUDIO_SESSION_SECONDS,
                    )
                    if limit_error:
                        client_state["is_recording"] = False
                        client_state["received_audio_buffer"].clear()
                        client_state["recording_start_time"] = None
                        await websocket.send_text(
                            json.dumps({"type": "error", "text": limit_error})
                        )
                        continue
                    client_state["received_audio_buffer"].extend(audio_chunk)

                    # Log progress
                    total_kb = len(client_state["received_audio_buffer"]) / 1024
                    chunk_kb = len(audio_chunk) / 1024
                    print(
                        f"Received audio chunk: {chunk_kb:.1f} KB, total: {total_kb:.1f} KB"
                    )
                else:
                    print("Received unexpected binary data when not recording")

    except Exception as e:
        print(f"WebSocket failed: {error_type(e)}")
    finally:
        client_state["is_recording"] = False
        client_state["received_audio_buffer"].clear()
        client_state["recording_start_time"] = None
        WS_CONNECTIONS.dec()
        _active_ws_connections.discard(websocket)
        _log.info("ws_disconnected", subject=pseudonymous_id(client_id))
        print("Client disconnected")
        # Close database connection
        # MongoDB DISABLED - Commented out
        # db.close()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
