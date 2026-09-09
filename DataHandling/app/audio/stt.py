"""Speech-to-Text — transcription service.

Primary:  configured, startup-validated Gemini audio model
  - Handles Indian English natively, understands medical context
  - Accepts webm/ogg directly — no pydub decode overhead
  - Typical latency: 2–4 s

Fallback: Google Cloud Speech-to-Text (synchronous)
  - Used only when Gemini fails
  - SpeechClient cached as module-level singleton to avoid per-call init cost

Anti-hallucination:
  - is_hallucination() detects repetitive / known-silence outputs
  - clean_transcript() strips filler words (uh, um, er …)
"""
import io
import os
import re
import sys
import logging

from app.ai.models import MODEL_REGISTRY
from app.observability.ai_usage import AIUsage, gemini_usage, tracked_ai_call
from app.observability.privacy import error_type

logger = logging.getLogger(__name__)

# ── Filler-word removal ──────────────────────────────────────────────────────
_FILLER_PATTERN = re.compile(
    r'\b(uh+|um+|ah+|er+|hmm+|hm+|mhm|uhm|umm|ehh?|ehhh?)\b[\s,]*',
    re.IGNORECASE,
)

def clean_transcript(text: str) -> str:
    """Strip filler words and normalise whitespace. No LLM needed."""
    if not text or not text.strip():
        return text
    result = _FILLER_PATTERN.sub(' ', text)
    result = re.sub(r'  +', ' ', result).strip()
    if result:
        result = result[0].upper() + result[1:]
    return result


# ── Hallucination detection ──────────────────────────────────────────────────
def is_hallucination(text: str) -> bool:
    """Return True if the transcription looks like an STT hallucination."""
    if not text:
        return False
    words = text.lower().split()
    if not words:
        return False

    # Highly repetitive (unique word ratio < 12%)
    if len(words) >= 15:
        if len(set(words)) / len(words) < 0.12:
            return True

    # Same 4-word phrase repeated 5+ times
    if len(words) >= 4:
        phrase = " ".join(words[:4])
        if text.lower().count(phrase) >= 5:
            return True

    # Known silence hallucinations
    _silence_hallucinations = {
        "thank you for watching",
        "thanks for watching",
        "thank you for listening",
        "please subscribe",
        "like and subscribe",
        "subtitles by",
        "transcribed by",
        "you",
    }
    if text.strip().lower().rstrip(".,!?") in _silence_hallucinations:
        return True

    return False


# ── Gemini audio transcription (primary) ─────────────────────────────────────
_GEMINI_STT_PROMPT = (
    "You are a medical transcription assistant. "
    "Transcribe EXACTLY what the patient says — word for word. "
    "This is a physiotherapy intake interview conducted in Indian English. "
    "The patient may describe pain, injuries, body parts, medical history, or lifestyle. "
    "Common terms: knee, lower back, shoulder, neck, MRI, X-ray, physiotherapy, "
    "orthopedic, diabetes, hypertension, pain scale 0-10. "
    "Do NOT paraphrase, summarise, or add any commentary. "
    "Return ONLY the transcription text, nothing else."
)

def _detect_mime_type(raw_bytes: bytes) -> str:
    """Detect audio MIME type from magic bytes."""
    if raw_bytes[:4] == b'\x1aE\xdf\xa3':
        return "audio/webm"
    if raw_bytes[:4] == b'OggS':
        return "audio/ogg"
    if raw_bytes[:4] == b'RIFF':
        return "audio/wav"
    if raw_bytes[:3] == b'ID3' or raw_bytes[:2] == b'\xff\xfb':
        return "audio/mp3"
    # Default to webm — most common from browsers
    return "audio/webm"


def _transcribe_with_gemini(raw_audio_bytes: bytes) -> str:
    """Transcribe audio using the configured Gemini audio model."""
    import time as _time
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("No GEMINI_API_KEY set")

    from google import genai as _genai
    from google.genai import types as _types

    mime_type = _detect_mime_type(raw_audio_bytes)
    client = _genai.Client(api_key=api_key)

    _t0 = _time.perf_counter()
    response = tracked_ai_call(
        provider="google_genai",
        model=MODEL_REGISTRY.audio,
        operation="audio_transcription",
        call=lambda: client.models.generate_content(
            model=MODEL_REGISTRY.audio,
            contents=[
                _types.Part.from_bytes(data=raw_audio_bytes, mime_type=mime_type),
                _GEMINI_STT_PROMPT,
            ],
        ),
        usage_extractor=gemini_usage,
    )
    _ms = (_time.perf_counter() - _t0) * 1000
    text = (response.text or "").strip()
    sys.stderr.write(f"[stt] gemini={_ms:.0f}ms mime={mime_type} chars={len(text)}\n")
    sys.stderr.flush()
    return text


# ── Google Cloud STT fallback ─────────────────────────────────────────────────
_MAX_CHUNK_SECONDS = 55  # stay safely under Google's 60-second sync limit
_gcloud_client = None   # singleton — avoids per-call credential loading

def _get_gcloud_client():
    global _gcloud_client
    if _gcloud_client is None:
        from google.cloud import speech
        _gcloud_client = speech.SpeechClient()
    return _gcloud_client


def _transcribe_with_gcloud(raw_audio_bytes: bytes) -> str:
    """Fallback: Google Cloud STT. Slower but reliable."""
    from google.cloud import speech
    from pydub import AudioSegment

    segment = None
    for fmt in ("webm", "ogg", "wav", "mp3"):
        try:
            segment = AudioSegment.from_file(io.BytesIO(raw_audio_bytes), format=fmt)
            break
        except Exception:
            continue

    if segment is None:
        raise ValueError("Could not decode audio — unsupported format")

    segment = segment.set_frame_rate(16000).set_channels(1).set_sample_width(2)
    audio_duration_seconds = len(segment) / 1000.0

    # Use latest_short for clips ≤30 s (faster), latest_long for longer audio
    model = "latest_short" if audio_duration_seconds <= 30 else "latest_long"

    client = _get_gcloud_client()
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code="en-IN",
        model=model,
        use_enhanced=True,
        enable_automatic_punctuation=True,
        speech_contexts=[
            speech.SpeechContext(
                phrases=[
                    "pain", "relief", "relieving", "aggravating", "severity",
                    "ice pack", "heat pack", "swelling", "stiffness",
                    "tingling", "numbness", "radiating", "sharp pain",
                    "dull ache", "throbbing", "intermittent", "constant",
                    "knee", "lower back", "upper back", "neck", "shoulder",
                    "hip", "ankle", "wrist", "elbow", "spine", "lumbar",
                    "cervical", "thoracic", "sacral", "sciatica",
                    "physiotherapy", "physiotherapist", "orthopedic",
                    "MRI", "X-ray", "CT scan", "ultrasound",
                    "diabetes", "hypertension", "thyroid", "blood pressure",
                    "surgery", "fracture", "dislocation", "sprain", "strain",
                    "exercise", "stretching", "strengthening",
                    "medication", "tablet", "injection", "steroid",
                    "Stance Health", "Sage",
                ],
                boost=15.0,
            )
        ],
    )

    if audio_duration_seconds <= _MAX_CHUNK_SECONDS:
        buf = io.BytesIO()
        segment.export(buf, format="wav")
        response = tracked_ai_call(
            provider="google_speech",
            model=f"v1-{model}-enhanced",
            operation="audio_transcription_fallback",
            call=lambda: client.recognize(
                config=config,
                audio=speech.RecognitionAudio(content=buf.getvalue()),
            ),
            submitted_usage=AIUsage(measured=True, audio_seconds=audio_duration_seconds),
        )
        results = list(response.results)
    else:
        max_ms = _MAX_CHUNK_SECONDS * 1000
        num_chunks = int(audio_duration_seconds / _MAX_CHUNK_SECONDS) + (
            1 if audio_duration_seconds % _MAX_CHUNK_SECONDS > 0 else 0
        )
        results = []
        for i in range(num_chunks):
            chunk = segment[i * max_ms: (i + 1) * max_ms]
            buf = io.BytesIO()
            chunk.export(buf, format="wav")
            chunk_seconds = len(chunk) / 1000.0
            resp = tracked_ai_call(
                provider="google_speech",
                model=f"v1-{model}-enhanced",
                operation="audio_transcription_fallback",
                call=lambda: client.recognize(
                    config=config,
                    audio=speech.RecognitionAudio(content=buf.getvalue()),
                ),
                submitted_usage=AIUsage(measured=True, audio_seconds=chunk_seconds),
            )
            results.extend(resp.results)

    transcripts = [r.alternatives[0].transcript for r in results if r.alternatives]
    return " ".join(transcripts)


# ── Silence padding ───────────────────────────────────────────────────────────
_SILENCE_PAD_MS = 300  # prepend 300 ms silence so STT VAD doesn't eat first syllable

def _pad_with_silence(raw_audio_bytes: bytes) -> bytes:
    """
    Prepend 300 ms of silence to a WAV or raw audio blob.
    For non-WAV formats (webm/ogg) we prepend silence as a WAV header trick
    by decoding, padding, and re-encoding — but only if pydub is available.
    Falls back to returning the original bytes unchanged on any error.
    """
    try:
        from pydub import AudioSegment
        segment = None
        for fmt in ("webm", "ogg", "wav", "mp3"):
            try:
                segment = AudioSegment.from_file(io.BytesIO(raw_audio_bytes), format=fmt)
                break
            except Exception:
                continue
        if segment is None:
            return raw_audio_bytes
        silence = AudioSegment.silent(duration=_SILENCE_PAD_MS,
                                      frame_rate=segment.frame_rate)
        padded = silence + segment
        buf = io.BytesIO()
        padded.export(buf, format="wav")
        return buf.getvalue()
    except Exception:
        return raw_audio_bytes


# ── Public API ────────────────────────────────────────────────────────────────
def transcribe_audio_bytes(raw_audio_bytes: bytes) -> str:
    """
    Transcribe raw audio bytes. Tries Gemini first, falls back to Google Cloud STT.
    """
    import time as _time
    _t0 = _time.perf_counter()
    raw_audio_bytes = _pad_with_silence(raw_audio_bytes)

    try:
        text = _transcribe_with_gemini(raw_audio_bytes)
        if text and len(text.strip()) > 1:
            sys.stderr.write(f"[timing] stt_total={(_time.perf_counter() - _t0) * 1000:.0f}ms source=gemini\n")
            sys.stderr.flush()
            return text
        sys.stderr.write("[stt] Gemini returned empty — falling back to Google Cloud STT\n")
        sys.stderr.flush()
    except Exception as e:
        sys.stderr.write(f"[stt] Gemini failed ({error_type(e)}) — falling back to gcloud\n")
        sys.stderr.flush()

    text = _transcribe_with_gcloud(raw_audio_bytes)
    sys.stderr.write(f"[timing] stt_total={(_time.perf_counter() - _t0) * 1000:.0f}ms source=gcloud\n")
    sys.stderr.flush()
    return text
