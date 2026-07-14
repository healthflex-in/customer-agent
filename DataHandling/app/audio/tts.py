"""
Microsoft Edge TTS — neural voices, completely free, no API key required.

Uses Microsoft's Azure neural TTS via the edge-tts package.
Default voice: en-IN-NeerjaNeural (Indian English female, warm and professional).
Fallback: en-US-JennyNeural (American English female).
"""
import asyncio
import io
from typing import Optional

DEFAULT_VOICE = "en-IN-NeerjaNeural"
FALLBACK_VOICE = "en-US-JennyNeural"

# 16kHz mono 16-bit — matches Whisper STT input format
WAV_SAMPLE_RATE = 16000
WAV_CHANNELS = 1
WAV_SAMPLE_WIDTH = 2


async def synthesize_mp3(text: str, voice: str = DEFAULT_VOICE) -> Optional[bytes]:
    """
    Generate MP3 audio from text using edge-tts neural voices.
    Returns raw MP3 bytes, or None on failure.
    """
    if not text or not text.strip():
        return None
    try:
        import edge_tts
        communicate = edge_tts.Communicate(text.strip(), voice)
        chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        return b"".join(chunks) if chunks else None
    except Exception as e:
        print(f"[tts] edge-tts synthesis failed ({voice}): {e}")
        # Try fallback voice once
        if voice != FALLBACK_VOICE:
            try:
                import edge_tts as _et
                communicate = _et.Communicate(text.strip(), FALLBACK_VOICE)
                chunks = []
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        chunks.append(chunk["data"])
                if chunks:
                    print(f"[tts] fallback voice succeeded")
                    return b"".join(chunks)
            except Exception as e2:
                print(f"[tts] fallback voice also failed: {e2}")
        return None


def mp3_to_wav(mp3_bytes: bytes) -> Optional[bytes]:
    """Convert MP3 bytes to 16kHz mono WAV bytes."""
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_mp3(io.BytesIO(mp3_bytes))
        seg = (
            seg.set_frame_rate(WAV_SAMPLE_RATE)
               .set_channels(WAV_CHANNELS)
               .set_sample_width(WAV_SAMPLE_WIDTH)
        )
        out = io.BytesIO()
        seg.export(out, format="wav")
        return out.getvalue()
    except Exception as e:
        print(f"[tts] mp3_to_wav failed: {e}")
        return None


async def text_to_wav(text: str, voice: str = DEFAULT_VOICE) -> Optional[bytes]:
    """Convenience: synthesize text → WAV bytes in one call."""
    mp3 = await synthesize_mp3(text, voice)
    if not mp3:
        return None
    return await asyncio.to_thread(mp3_to_wav, mp3)
