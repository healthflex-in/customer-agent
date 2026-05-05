"""
This script demonstrates how to use the SpeechRecognition library to listen for speech and process it.
Took inspiration from cli_chat.py and cli_chat_whisper_rt.py, located in the "legacy" directory of the branch.
"""

import whisper
import numpy as np
import pyttsx3
import os
import time
import torch
import webrtcvad
import pyaudio

from src.llm.functionalities import HealthAgent


# Get CUDA device
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


cuda_device = get_cuda_device()

# Initialize Whisper Model
model = whisper.load_model(name="base", device=cuda_device)

# Initialize HealthAgent
health_agent = HealthAgent()
engine = pyttsx3.init()
history = []


class VoiceActivityDetector:
    """Class for handling Voice Activity Detection (VAD) functionality."""

    def __init__(self):
        # Audio parameters
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000  # WebRTC VAD works best with 16kHz
        self.FRAME_DURATION_MS = (
            30  # Frame size in ms (must be 10, 20, or 30ms for WebRTC VAD)
        )
        self.FRAME_SIZE = int(self.RATE * self.FRAME_DURATION_MS / 1000)
        self.SILENCE_THRESHOLD_SEC = 1.5  # 1.5 seconds of silence to stop recording
        self.INITIAL_LISTEN_SEC = 10  # Initial listening period in seconds
        self.VAD_AGGRESSIVENESS = 2  # Mode 3 is more aggressive in filtering non-speech and mode 1 is standard

    def record_with_vad(self, timeout=60):
        """
        Records audio using WebRTC VAD to detect speech and silence.

        Args:
            timeout (int): Maximum recording time in seconds

        Returns:
            numpy.ndarray: Recorded audio as numpy array
        """
        # Initialize WebRTC VAD
        self.vad = webrtcvad.Vad(self.VAD_AGGRESSIVENESS)

        # Initialize PyAudio
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            frames_per_buffer=self.FRAME_SIZE,
        )

        buffer = []
        is_speaking = False
        silent_frames = 0
        required_silent_frames = int(
            self.SILENCE_THRESHOLD_SEC * 1000 / self.FRAME_DURATION_MS
        )
        initial_frames = int(self.INITIAL_LISTEN_SEC * 1000 / self.FRAME_DURATION_MS)
        frames_processed = 0

        print("Listening... (speak now)")
        start_time = time.time()

        try:
            initial_listening_ended = False
            initial_end_time = start_time + self.INITIAL_LISTEN_SEC

            while time.time() - start_time < timeout:
                # Read audio frame
                data = stream.read(self.FRAME_SIZE, exception_on_overflow=False)
                frames_processed += 1

                # Check for speech with VAD
                is_speech = self.vad.is_speech(data, self.RATE)

                # Initial listening period logic
                if not initial_listening_ended:
                    # Add data to buffer regardless during initial period
                    buffer.append(data)

                    # If speech detected, mark as speaking
                    if is_speech:
                        is_speaking = True
                        silent_frames = 0

                    # If user starts speaking and then stops during initial period,
                    # check if they've been silent for the threshold
                    if is_speaking and not is_speech:
                        silent_frames += 1
                        if silent_frames >= required_silent_frames:
                            print(
                                f"Silence detected for {self.SILENCE_THRESHOLD_SEC}s, stopping recording"
                            )
                            initial_listening_ended = True
                            break

                    # Check if initial period is over
                    if time.time() >= initial_end_time:
                        initial_listening_ended = True

                        # If no speech was detected at all during initial period, return empty
                        if not is_speaking and not is_speech:
                            print("No speech detected during initial listening period")
                            break

                    continue

                # After initial period, standard VAD processing
                if is_speech:
                    if not is_speaking:
                        is_speaking = True
                    silent_frames = 0
                    buffer.append(data)
                else:
                    if is_speaking:
                        silent_frames += 1
                        buffer.append(data)

                        if silent_frames >= required_silent_frames:
                            print(
                                f"Silence detected for {self.SILENCE_THRESHOLD_SEC}s, stopping recording"
                            )
                            break
                    else:
                        # Only add silence to buffer if we're already speaking
                        pass

                # Safety check to avoid infinite recording
                if len(buffer) * self.FRAME_DURATION_MS / 1000 > timeout:
                    print(f"Maximum recording time ({timeout}s) reached")
                    break

        except KeyboardInterrupt:
            print("\nRecording interrupted")
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()

        if not buffer:
            print("No audio recorded")
            return np.array([], dtype=np.int16)

        # Convert buffer to numpy array
        audio_data = b"".join(buffer)
        return np.frombuffer(audio_data, dtype=np.int16)


def speak_text(text):
    """Convert text to speech."""
    try:
        engine.say(text)
        engine.runAndWait()
    except Exception as e:
        print(f"TTS Error: {e}")


def transcribe_audio(audio, samplerate=16000):
    """Transcribe recorded audio using Whisper."""
    try:
        if len(audio) == 0:
            print("Empty audio, nothing to transcribe")
            return None

        print("Processing response...")
        # Convert to float32 and normalize
        audio = audio.astype(np.float32) / 32768.0

        # Whisper expects a certain format, make sure our audio is compatible
        audio = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio).to(model.device)
        options = whisper.DecodingOptions(fp16=torch.cuda.is_available())
        result = whisper.decode(model, mel, options)

        text = result.text.strip()
        if text:
            print(f"Transcribed: {text}")
            return text
        else:
            print("No speech detected.")
            return None
    except Exception as e:
        print(f"Error with speech transcription: {e}")
        return None


def process_user_input(text):
    """Process user input and get response from HealthAgent."""
    if not text:
        return "I didn't catch that. Could you please repeat?"

    response = health_agent.main_processor(text)
    history.append({"role": "user", "message": text})
    history.append({"role": "agent", "message": response})

    return response


def display_progress():
    """Display interview progress."""
    if not health_agent.form_sections:
        return

    try:
        current_index = health_agent.form_sections.index(health_agent.current_section)
        total_sections = len(health_agent.form_sections)
        progress = (current_index / total_sections) * 100

        if health_agent.missing_fields:
            total_fields = len(health_agent.form[health_agent.current_section])
            if total_fields > 0:
                section_progress = (
                    total_fields - len(health_agent.missing_fields)
                ) / total_fields
                section_contribution = (section_progress / total_sections) * 100
                progress += section_contribution

        progress = min(progress, 99)
        bar_length = 30
        filled_length = int(bar_length * progress / 100)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        print(f"\nInterview Progress: |{bar}| {progress:.1f}%\n")
    except Exception as e:
        print(f"Error displaying progress: {e}")


def main_interview_loop():
    """Main loop for conducting the interview."""
    # instructions = "Welcome to the Medical Interview System. Please speak clearly. Say 'exit' anytime to stop."
    # print("\n" + instructions)
    # speak_text(instructions)
    print("\n")

    # Initialize the VAD detector
    vad_detector = VoiceActivityDetector()

    try:
        while True:
            display_progress()

            user_audio = vad_detector.record_with_vad()

            if len(user_audio) == 0:
                print("\nI'm sorry, I could not hear anything. Let's try again.")
                continue

            user_text = transcribe_audio(user_audio)

            if user_text:
                if user_text.lower() in ["exit", "stop", "quit"]:
                    print("Exiting interview...")
                    health_agent.save_progress()
                    break

                agent_response = process_user_input(user_text)
                print(f"\nMEDICAL ASSISTANT: {agent_response}\n")
                speak_text(agent_response)
            else:
                print(
                    "\nI didn't hear anything clear enough to understand. Please try again."
                )

    except KeyboardInterrupt:
        print("\nInterview interrupted. Saving progress...")
        health_agent.save_progress()
        print("Progress saved. Goodbye!")


def start_cli_interview():
    """Start the medical interview CLI."""
    os.system("cls" if os.name == "nt" else "clear")
    print("=" * 60)
    print("MEDICAL INTERVIEW ASSISTANT - COMMAND LINE INTERFACE")
    print("=" * 60)

    intro_message = (
        "Hello! I'm your medical interview assistant. This interview will take approximately 5 to 10 minutes to complete. "
        "Are you free to proceed now? If not, we can schedule it for another time. "
        "Say 'yes' to begin or 'no' to postpone."
    )

    print("\n" + intro_message)
    speak_text(intro_message)

    main_interview_loop()


if __name__ == "__main__":
    start_cli_interview()
