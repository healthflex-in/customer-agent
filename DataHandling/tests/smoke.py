"""Smoke test for the healthflex-agent server.

Run against a locally-running container (default http://localhost:8000).
Exits 0 on success, non-zero on any failed assertion.

Usage:
    python tests/smoke.py                       # localhost:8000
    BASE_URL=http://localhost:8001 python tests/smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from urllib.parse import urlparse

import requests
import websockets

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
WS_URL = BASE_URL.replace("http://", "ws://").replace("https://", "wss://")
TIMEOUT = 30


def step(name: str) -> None:
    print(f"\n— {name}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    sys.exit(1)


def check_health() -> None:
    step("GET /health")
    r = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
    if r.status_code != 200:
        fail(f"expected 200, got {r.status_code}")
    ok(f"200 OK — body: {r.text[:120]}")


async def ws_protocol_check() -> None:
    """
    Verify the WS endpoint accepts a connection and validates input.

    We deliberately send `start_interview` with no `userId` — the server should
    respond with `{"type": "error", "message": "...userId..."}`. This proves
    the WS pipeline (accept → dispatch → reply) end-to-end without creating
    any real interview session or writing user state to the DB.
    """
    step("WebSocket /ws/<client_id> — protocol validation")
    client_id = f"smoke-{int(time.time())}"
    uri = f"{WS_URL}/ws/{client_id}"
    try:
        async with websockets.connect(uri, open_timeout=TIMEOUT) as ws:
            ok(f"connected to {uri}")
            await ws.send(json.dumps({"type": "start_interview"}))
            ok("sent start_interview (no userId — expecting structured error)")

            inbound = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
            try:
                payload = json.loads(inbound)
            except Exception:
                fail(f"non-JSON inbound: {inbound[:200]}")
                return

            msg_type = payload.get("type")
            if msg_type != "error":
                fail(f"expected type=error (missing-userId path), got {msg_type}")
            msg_text = (payload.get("message") or payload.get("text") or "").lower()
            if "userid" not in msg_text:
                fail(f"error payload didn't mention userId: {payload}")
            ok(f"received expected validation error — {str(payload)[:160]}")

            await ws.send(json.dumps({"type": "end_session"}))
            ok("sent end_session, closing")
    except Exception as e:
        fail(f"WS test error: {e}")


def main() -> None:
    print(f"Smoke test against {BASE_URL}")
    parsed = urlparse(BASE_URL)
    if not parsed.netloc:
        fail("BASE_URL invalid")
    check_health()
    asyncio.run(ws_protocol_check())
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
