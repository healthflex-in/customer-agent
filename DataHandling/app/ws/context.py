"""Per-WebSocket-connection context passed to stage handlers."""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import asyncio

@dataclass
class WSContext:
    websocket: Any              # fastapi.WebSocket
    client_id: str
    client_state: dict          # mutable per-connection state (graph_* keys etc.)
    agent: Any                  # per-connection HealthAgent instance
    graph: Any                  # compiled LangGraph (shared, stateless)
    session_lock: asyncio.Lock  # prevents text+audio race condition
    save_fn: Callable           # _save_for_graph closure
    fetch_form_fn: Callable     # fetch_form_by_id
    fetch_attachments_fn: Callable
    fetch_latest_form_fn: Callable
    send_thought_fn: Callable   # _send_thought_update partial
