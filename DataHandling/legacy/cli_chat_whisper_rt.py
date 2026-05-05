import whisper
import sounddevice as sd
import numpy as np
import queue
import pyttsx3
from src.llm.functionalities import HealthAgent

model = whisper.load_model("tiny")
audio_queue = queue.Queue()

# define ChatUI object below and replace reverse_text function
# obj = HealthAgent()


def reverse_text(text):
    # placeholder function, replace with actual work
    # response = obj.main_processor()
    # return response
    return text[::-1]


def audio_callback(indata, frames, time, status):
    if status:
        print(status)
    audio_queue.put(indata.copy())  # audio chunks


def transcribe_audio(audio_data):
    audio = audio_data.flatten()
    audio = whisper.pad_or_trim(audio)
    spect = whisper.log_mel_spectrogram(audio).to(
        model.device)
    options = whisper.DecodingOptions(fp16=False)
    result = whisper.decode(model, spect, options)
    return result.text


def speak_text(text):
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


if __name__ == "__main__":
    samplerate = 16000
    k = 2  # num blocks
    blocksize = 4096*k

    with sd.InputStream(callback=audio_callback, samplerate=samplerate,
                        channels=1, dtype=np.float32, blocksize=blocksize):
        print("listening in real time...")
        try:
            while True:
                if not audio_queue.empty():
                    audio_chunk = audio_queue.get()
                    transcribed_text = transcribe_audio(audio_chunk)

                    if transcribed_text.strip():
                        print(f"recognized: {transcribed_text}")
                        reversed_text = reverse_text(transcribed_text)
                        print(f"Reversed: {reversed_text}")
                        speak_text(reversed_text)
        except KeyboardInterrupt:
            print("stopping...")
