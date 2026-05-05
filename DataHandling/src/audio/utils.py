import numpy as np
from transformers import pipeline
import os
import tempfile
from gtts import gTTS
import time
from pydub import AudioSegment
import torch


def get_cuda_device():
    if torch.cuda.is_available():
        device_id = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(device_id)
        cuda_device = f"cuda:{device_id}"
        print(f"Using {device_name} on {cuda_device}")
        return cuda_device
    else:
        print("CUDA is not available. Falling back to CPU.")
        return "cpu"


def init_transcriber():
    """
    Initialize the whisper transcriber.
    """

    return pipeline(
        "automatic-speech-recognition",
        model="openai/whisper-base.en",
        device=get_cuda_device(),
    )


def transcribe_audio(transcriber, audio):
    """
    Transcribe audio using whisper.
    """

    sr, y = audio

    # Convert to mono if stereo
    if y.ndim > 1:
        y = y.mean(axis=1)

    y = y.astype(np.float32)
    y /= np.max(np.abs(y))

    return transcriber({"sampling_rate": sr, "raw": y})["text"]


def generate_tts(text, speed_factor=1.5):
    """
    Generate TTS with adjusted speed and return file path.
    """

    unique_id = str(int(time.time()))
    temp_file = os.path.join(tempfile.gettempdir(), f"audio_{unique_id}.mp3")

    try:
        # Generate the initial slow audio
        tts = gTTS(text=text, lang="en", slow=False)
        temp_slow_file = temp_file.replace(".mp3", "_slow.mp3")
        tts.save(temp_slow_file)

        # Load and speed up using pydub
        audio = AudioSegment.from_file(temp_slow_file, format="mp3")
        faster_audio = audio.speedup(playback_speed=speed_factor)

        # Export the faster version
        faster_audio.export(temp_file, format="mp3")
        os.remove(temp_slow_file)  # Cleanup original file

        return temp_file
    except Exception as e:
        print(f"Error generating TTS: {str(e)}")
        return None
