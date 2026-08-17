"""Thin client for the clinical-mcp MCP server.

Only active in dev when MCP_URL is set. Returns question recommendations
that Sage uses as topic hints during the interview.
"""
import json
import os

_MCP_URL: str = os.environ.get("MCP_URL", "")
_TIMEOUT: int = 6  # seconds — fast enough not to block a turn


def is_available() -> bool:
    return bool(_MCP_URL)


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
    if not _MCP_URL:
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
        resp = requests.post(
            _MCP_URL,
            json=payload,
            headers={"Accept": "application/json, text/event-stream"},
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
        print(f"[mcp_client] {len(recs)} recommendations for: {case_description[:60]!r}")
        return recs

    except Exception as e:
        print(f"[mcp_client] Non-fatal: {e}")
        return []
