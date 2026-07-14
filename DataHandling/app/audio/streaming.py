"""Stream audio bytes to WebSocket client in chunks."""
import json, base64

MAX_CHUNK_SIZE = 32768  # 32KB

async def stream_audio_to_client(websocket, audio_data: bytes, message_id: str) -> None:
    chunk_size = MAX_CHUNK_SIZE
    total_size = len(audio_data)
    num_chunks = (total_size + chunk_size - 1) // chunk_size
    await websocket.send_text(json.dumps({
        "type": "audio_start",
        "message_id": message_id,
        "total_size": total_size,
    }))
    for i in range(num_chunks):
        chunk = audio_data[i*chunk_size:(i+1)*chunk_size]
        is_last = (i == num_chunks - 1)
        await websocket.send_text(json.dumps({
            "type": "audio_chunk",
            "message_id": message_id,
            "data": base64.b64encode(chunk).decode("utf-8"),
            "is_last": is_last,
        }))
