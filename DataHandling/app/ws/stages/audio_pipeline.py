"""Handle audio_end: transcribe with Whisper and send transcription to client."""
import json
import io
import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ws.context import WSContext

RATE = 16000
SAMPLE_WIDTH = 2

async def handle(ctx: "WSContext", data: dict) -> None:
    ws = ctx.websocket
    cs = ctx.client_state
    cs["is_recording"] = False

    audio_bytes = cs.get("received_audio_buffer", b"")
    duration = data.get("duration", 0)

    if len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5:
        await ws.send_text(json.dumps({"type": "error", "text": "Audio too short to process"}))
        return

    try:
        import time
        from pydub import AudioSegment
        from app.audio.stt import get_model, is_hallucination

        timestamp = int(time.time())

        # Decode audio
        audio_segment = None
        for fmt in ("webm", "ogg", "wav", "mp3"):
            try:
                audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format=fmt)
                break
            except Exception:
                continue

        if audio_segment is None:
            await ws.send_text(json.dumps({"type": "error", "text": "Could not decode audio"}))
            return

        # Convert to PCM
        audio_segment = audio_segment.set_frame_rate(RATE).set_channels(1).set_sample_width(SAMPLE_WIDTH)
        pcm = audio_segment.raw_data
        audio_secs = len(pcm) / (RATE * SAMPLE_WIDTH)

        import numpy as np
        audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        model = get_model()
        segments, info = await asyncio.to_thread(
            lambda: model.transcribe(
                audio_np,
                beam_size=5,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
                # Anti-hallucination settings
                condition_on_previous_text=False,  # prevents context bleeding
                no_speech_threshold=0.6,           # stricter silence detection
                compression_ratio_threshold=2.4,   # reject repetitive outputs
                log_prob_threshold=-1.0,            # reject uncertain outputs
                temperature=0,                     # deterministic, no sampling
                language="en",                     # force English (no language guessing)
            )
        )
        transcription = "".join(s.text for s in segments).strip()
        print(f"[stt] Transcribed {audio_secs:.1f}s → {len(transcription)} chars, lang={getattr(info, 'language', 'en')}")

        if is_hallucination(transcription):
            await ws.send_text(json.dumps({"type": "error", "text": "Audio unclear — please speak again."}))
            return

        await ws.send_text(json.dumps({
            "type": "transcription",
            "text": transcription,
            "timestamp": timestamp,
        }))
        print(f"[audio] Transcribed: {transcription[:80]!r}")

    except Exception as e:
        import traceback
        print(f"[audio] Error: {e}\n{traceback.format_exc()}")
        await ws.send_text(json.dumps({"type": "error", "text": "Audio processing failed"}))
