"""WebSocket audio completion for an interview already in progress.

The connection loop in server.py accepts the socket and decides which command
may run. This handler transcribes the recording. The typed answer that follows
is a separate command.
"""

import json
import time
import uuid

from fastapi import WebSocket

from app.audio.limits import audio_limit_error, webm_header_offset
from app.audit.chat_history import build_pending_voice_input
from app.config import (
    AUDIO_RATE as RATE,
    AUDIO_SAMPLE_WIDTH as SAMPLE_WIDTH,
    DEBUG_AUDIO_CAPTURE,
    DEBUG_AUDIO_DIR,
    DEBUG_AUDIO_MAX_TOTAL_BYTES,
    DEBUG_AUDIO_RETENTION_HOURS,
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
    _log.info(
        "audio_recording",
        server_elapsed_seconds=round(elapsed_seconds, 2), declared_seconds=round(audio_duration, 2),
        head=audio_bytes[:12].hex(),
        chunks=client_state.get("audio_chunks_seen"),
        ebml_header_at=webm_header_offset(audio_bytes),
        kbps=round(len(audio_bytes) * 8 / 1000 / audio_duration) if audio_duration else 0,
    )

    # Optional QA-only capture of the original browser audio.
    # The default remains memory-only. Files live only in the
    # container filesystem and are age/size pruned. Capture
    # before the minimum-length check so failed short clips
    # are available when diagnosing recorder/transcription
    # problems.
    debug_audio_capture = None
    if DEBUG_AUDIO_CAPTURE and audio_bytes:
        try:
            from app.audio.debug_capture import save_debug_audio

            debug_audio_capture = await run_blocking(
                save_debug_audio,
                audio_bytes,
                enabled=True,
                directory=DEBUG_AUDIO_DIR,
                retention_hours=DEBUG_AUDIO_RETENTION_HOURS,
                max_total_bytes=DEBUG_AUDIO_MAX_TOTAL_BYTES,
                metadata={
                    "userId": client_state.get("user_id"),
                    "sessionId": client_state.get("session_id"),
                    "attemptId": client_state.get("attempt_id"),
                    "interviewId": client_state.get("interview_id"),
                    "formId": client_state.get("form_id"),
                    "declaredDurationSeconds": audio_duration,
                    "measuredDurationSeconds": elapsed_seconds,
                },
            )
            if debug_audio_capture is not None:
                print(
                    "[audio-debug] Saved capture "
                    f"{debug_audio_capture.capture_id}"
                )
        except Exception as debug_error:
            print(
                "[audio-debug] Capture failed: "
                f"{error_type(debug_error)}"
            )

    async def update_audio_debug_status(**updates):
        """Keep optional debug writes from affecting STT delivery."""
        if debug_audio_capture is None:
            return
        try:
            from app.audio.debug_capture import update_debug_capture

            await run_blocking(
                update_debug_capture,
                debug_audio_capture,
                **updates,
            )
        except Exception as debug_error:
            print(
                "[audio-debug] Status update failed: "
                f"{error_type(debug_error)}"
            )

    # Skip processing if not enough audio data received
    if (
        len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5
    ):  # At least 0.5 seconds
        await update_audio_debug_status(status="rejected_too_short")
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
        raw_transcription = transcription
        t_stt_ms = (time.perf_counter() - t_stt_start) * 1000
        print(f"[audio] STT latency={t_stt_ms:.0f}ms transcript_chars={len(transcription)}")
        await update_audio_debug_status(
            status="transcribed",
            raw_transcript=raw_transcription,
        )
        if contains_internal_transcription_prompt(transcription):
            await update_audio_debug_status(status="rejected_internal_prompt")
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
            await update_audio_debug_status(status="rejected_hallucination")
            print("[audio] Hallucination detected — discarding transcription")
            await websocket.send_text(json.dumps({
                "type": "error",
                "text": "Audio unclear — please try speaking again.",
            }))
            return
        transcription = clean_transcript(transcription)
        await update_audio_debug_status(
            status="delivered",
            cleaned_transcript=transcription,
        )

        # The server remembers which transcription it delivered so the
        # patient's next submitted answer can be audited as voice input
        # without trusting file metadata sent by the client.
        transcription_id = f"TRN-{uuid.uuid4()}"
        audio_capture_id = (
            debug_audio_capture.capture_id
            if debug_audio_capture is not None
            else None
        )
        audio_filename = (
            debug_audio_capture.audio_path.name
            if debug_audio_capture is not None
            else None
        )
        client_state["pending_voice_input"] = build_pending_voice_input(
            transcript=transcription,
            transcription_id=transcription_id,
            audio_capture_id=audio_capture_id,
            audio_filename=audio_filename,
        )

        # Send transcription immediately to frontend for real-time display
        transcription_payload = {
            "type": "transcription",
            "text": transcription,
            "timestamp": timestamp,
            "inputSource": "voice_transcription",
            "transcriptionId": transcription_id,
        }
        if audio_capture_id:
            transcription_payload["audioCaptureId"] = audio_capture_id
        if audio_filename:
            transcription_payload["audioFilename"] = audio_filename
        await websocket.send_text(json.dumps(transcription_payload))
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
        await update_audio_debug_status(
            status="transcription_error",
            error_code=error_type(e),
        )
        print(f"[audio] Processing failed: {error_type(e)}")
        await websocket.send_text(
            json.dumps(
                {
                    "type": "error", 
                    "text": "Unable to process audio. Please try again or type your response."
                }
            )
        )
