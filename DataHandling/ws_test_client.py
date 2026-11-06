"""Minimal WebSocket smoke test against a running customer-agent instance.

Runs from your HOST machine against the published Docker port — it does not
need to run inside the container. Only needs the `websockets` package:

    pip install websockets
    python3 ws_test_client.py --user-id <real_user_id_from_mongo>

Find a real user ID first:
    curl -s http://localhost:8004/api/users | python3 -m json.tool
"""
import argparse
import asyncio
import json

import websockets


async def main(url: str, user_id: str) -> None:
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({"type": "start_interview", "userId": user_id}))
        print(">> sent start_interview")
        for _ in range(6):
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                print("(no more messages within 30s)")
                break
            msg = json.loads(raw)
            preview = json.dumps(msg, indent=2)
            print(f"<< {msg.get('type')}: {preview[:600]}")
            if msg.get("type") in ("text_message", "form_completed", "error"):
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8004/ws/manual-test-1")
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    asyncio.run(main(args.url, args.user_id))
