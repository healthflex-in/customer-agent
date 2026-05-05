import whisper
import sounddevice as sd
import numpy as np
import pyttsx3
from src.llm.functionalities import HealthAgent

model = whisper.load_model("tiny")
# define ChatUI object below and replace reverse_text function
# obj = HealthAgent()


def reverse_text(text):
    # placeholder function, replace with actual work
    # response = obj.main_processor()
    # return response
    return text[::-1]


def record_audio(duration=5, samplerate=16000):
    print("listening...")
    audio = sd.rec(int(samplerate * duration),
                   samplerate=samplerate,
                   channels=1, dtype=np.float32)
    sd.wait()
    return audio.flatten()


def transcribe_audio(audio, samplerate=16000):
    audio = whisper.pad_or_trim(audio)
    mel = whisper.log_mel_spectrogram(audio).to(
        model.device)
    options = whisper.DecodingOptions(fp16=False)
    result = whisper.decode(model, mel, options)
    return result.text


def speak_text(text):
    # tts
    engine = pyttsx3.init()
    engine.say(text)
    engine.runAndWait()


if __name__ == "__main__":
    while True:
        audio_data = record_audio(duration=5)
        transcribed_text = transcribe_audio(
            audio_data)
        print(f"recognized: {transcribed_text}")

        reversed_text = reverse_text(transcribed_text)
        print(f"processed: {reversed_text}")

        speak_text(reversed_text)
