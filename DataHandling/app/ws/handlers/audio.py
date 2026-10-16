"""WebSocket audio completion for an interview already in progress.

The connection loop in server.py accepts the socket and decides which command
may run. This handler transcribes the recording. The typed answer that follows
is a separate command.
"""

import json
import time

from fastapi import WebSocket

from app.audio.limits import audio_limit_error
from app.config import (
    AUDIO_RATE as RATE,
    AUDIO_SAMPLE_WIDTH as SAMPLE_WIDTH,
    MAX_AUDIO_SESSION_BYTES,
    MAX_AUDIO_SESSION_SECONDS,
)
from app.content_safety import contains_internal_transcription_prompt
from app.observability.logging import log as _log
from app.observability.privacy import error_type, pseudonymous_id
from app.runtime.blocking import run_blocking


async def handle_audio_end(
    websocket: WebSocket,
    data: dict,
    client_state: dict,
) -> None:
    """Transcribe the recording the patient just finished."""
    # Client has finished sending audio
    client_state["is_recording"] = False
    # Process the accumulated audio
    audio_bytes = bytes(client_state["received_audio_buffer"])
    client_state["received_audio_buffer"].clear()
    started_at = client_state.get("recording_start_time")
    elapsed_seconds = (
        max(0.0, time.monotonic() - started_at)
        if isinstance(started_at, (int, float))
        else 0.0
    )
    client_state["recording_start_time"] = None
    try:
        audio_duration = float(data.get("duration", 0) or 0)
    except (TypeError, ValueError):
        audio_duration = 0.0
    limit_error = audio_limit_error(
        current_bytes=len(audio_bytes),
        incoming_bytes=0,
        elapsed_seconds=elapsed_seconds,
        max_total_bytes=MAX_AUDIO_SESSION_BYTES,
        max_duration_seconds=MAX_AUDIO_SESSION_SECONDS,
        declared_duration_seconds=audio_duration,
    )
    if limit_error:
        await websocket.send_text(
            json.dumps({"type": "error", "text": limit_error})
        )
        return
    print(
        f"Received complete audio: {len(audio_bytes)} bytes, duration: {audio_duration:.2f}s"
    )
    # Skip processing if not enough audio data received
    if (
        len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5
    ):  # At least 0.5 seconds
        print(
            f"Audio too short ({len(audio_bytes)} bytes), skipping"
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error",
                    "text": "Audio too short to process",
                }
            )
        )
        return
    # timestamp still needed downstream for the outbound
    # 'transcription' WS message; the debug .raw write was removed.
    timestamp = int(time.time())
    # Transcribe through the configured STT service (Gemini with
    # an independent Google Cloud Speech fallback).
    try:
        from app.audio.stt import transcribe_audio_bytes, is_hallucination, clean_transcript
        t_stt_start = time.perf_counter()
        transcription = await run_blocking(
            transcribe_audio_bytes, audio_bytes
        )
        t_stt_ms = (time.perf_counter() - t_stt_start) * 1000
        print(f"[audio] STT latency={t_stt_ms:.0f}ms transcript_chars={len(transcription)}")
        if contains_internal_transcription_prompt(transcription):
            _log.warning(
                "internal_prompt_content_rejected_before_transcription_delivery",
                subject=pseudonymous_id(client_state.get("user_id")),
            )
            await websocket.send_text(json.dumps({
                "type": "error",
                "text": "The transcription could not be processed safely. Please record it again or type your response.",
            }))
            return
        if is_hallucination(transcription):
            print("[audio] Hallucination detected — discarding transcription")
            await websocket.send_text(json.dumps({
                "type": "error",
                "text": "Audio unclear — please try speaking again.",
            }))
            return
        transcription = clean_transcript(transcription)
        # Send transcription immediately to frontend for real-time display
        await websocket.send_text(
            json.dumps(
                {
                    "type": "transcription",
                    "text": transcription,
                    "timestamp": timestamp,
                }
            )
        )
        # (transcript .txt write removed — never read back)
        # Log user's speech in the database
        # MongoDB DISABLED - Commented out
        # db.log_interaction(
        #     user_id=client_state["user_id"],
        #     interview_id=client_state["interview_id"],
        #     interaction_type="user_speech",
        #     content=transcription,
        #     metadata={
        #         "audio_duration": audio_duration,
        #         "audio_file": file_path,
        #         "transcript_file": transcript_path,
        #     },
        # )
        # DO NOT process transcription automatically here
        # Wait for user to click send button, which will send a text_input message
        # The text_input handler (line 751) will process it and send the response
        print(f"Transcription sent to frontend. Waiting for user to click send...")
    except Exception as e:
        print(f"[audio] Processing failed: {error_type(e)}")
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error", 
                    "text": "Unable to process audio. Please try again or type your response."
                }
            )
        )
