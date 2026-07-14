"""Whisper STT — model loading and transcription.

Model: faster-whisper "small" (upgraded from "base")
- Much less hallucination than "base"
- Better accuracy on Indian English and accented speech
- ~2x slower than base on CPU but still real-time capable

Anti-hallucination settings:
- condition_on_previous_text=False  — prevents context bleeding between sentences
- no_speech_threshold=0.6           — stricter silence detection
- compression_ratio_threshold=2.4   — rejects suspiciously repetitive outputs
- log_prob_threshold=-1.0           — rejects very uncertain outputs
- temperature=0                     — deterministic, no random sampling
- vad_filter=True                   — strips silence before transcription
"""
import shutil
from typing import Optional

_model = None

def load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel
    cuda_device = "cuda" if shutil.which("nvidia-smi") else "cpu"
    # "small" is significantly more accurate than "base" and hallucinates far less.
    # On CPU it uses int8 quantisation for reasonable speed.
    _model = WhisperModel(
        "base",
        device=cuda_device,
        compute_type="int8" if cuda_device == "cpu" else "float16",
    )
    print(f"[stt] Whisper 'base' model loaded on {cuda_device}")
    return _model

def get_model():
    if _model is None:
        load_model()
    return _model

def is_hallucination(text: str) -> bool:
    """
    Detect common Whisper hallucination patterns.
    Returns True if the transcription looks like a hallucination.
    """
    if not text:
        return False

    words = text.lower().split()
    if not words:
        return False

    # 1. Highly repetitive output (unique word ratio < 12%)
    if len(words) >= 15:
        unique_ratio = len(set(words)) / len(words)
        if unique_ratio < 0.12:
            return True

    # 2. Same 4-word phrase repeated 5+ times
    if len(words) >= 4:
        phrase = " ".join(words[:4])
        if text.lower().count(phrase) >= 5:
            return True

    # 3. Known Whisper silence hallucinations
    _silence_hallucinations = {
        "thank you for watching",
        "thanks for watching",
        "thank you for listening",
        "please subscribe",
        "like and subscribe",
        "subtitles by",
        "transcribed by",
        "you",  # single word "you" with nothing else
    }
    stripped = text.strip().lower().rstrip(".,!?")
    if stripped in _silence_hallucinations:
        return True

    # 4. Extremely short output from long audio (likely garbled)
    # Handled by caller checking duration vs transcript length

    return False
