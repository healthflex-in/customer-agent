"""Thin client for the clinical-mcp MCP server.

Only active in dev when MCP_URL is set. Returns question recommendations
that Sage uses as topic hints during the interview.
"""
import json
import os
from urllib.parse import urlsplit

from app.observability.privacy import env_flag, error_type

_TIMEOUT: int = 6  # seconds — fast enough not to block a turn


def is_available() -> bool:
    return bool(os.environ.get("MCP_URL", "").strip()) and env_flag(
        "MCP_ALLOW_PHI",
        default=False,
    )


def _is_local_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname in {"localhost", "127.0.0.1", "::1"}


def _parse_sse(body: str) -> dict:
    """Extract the JSON payload from an SSE event: message / data: {...} response."""
    for line in body.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    pass
    return {}


def recommend_questions(case_description: str, limit: int = 8) -> list[dict]:
    """
    Call clinical-mcp's recommend_questions tool.

    Returns a list of dicts like:
      {"text": "...", "category": "...", "relevanceScore": 0.9}

    Returns [] on any error so the caller never has to handle exceptions.
    """
    mcp_url = os.environ.get("MCP_URL", "").strip()
    if not mcp_url or not is_available():
        return []

    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not _is_local_url(mcp_url) and not auth_token:
        print("[mcp_client] Remote PHI transfer blocked: authentication is not configured")
        return []

    import requests

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "recommend_questions",
            "arguments": {"caseDescription": case_description, "limit": limit},
        },
    }

    try:
        headers = {"Accept": "application/json, text/event-stream"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        resp = requests.post(
            mcp_url,
            json=payload,
            headers=headers,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()

        body = resp.text.strip()
        parsed = _parse_sse(body) if body.startswith("event:") or body.startswith("data:") else resp.json()

        content_text = (
            parsed.get("result", {})
            .get("content", [{}])[0]
            .get("text", "{}")
        )
        data = json.loads(content_text)
        recs = data.get("recommendations", [])
        print(f"[mcp_client] Received {len(recs)} recommendations")
        return recs

    except Exception as e:
        print(f"[mcp_client] Request failed: {error_type(e)}")
        return []
