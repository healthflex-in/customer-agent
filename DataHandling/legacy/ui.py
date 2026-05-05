import gradio as gr
import numpy as np
import random
import os
import time
import tempfile
from gtts import gTTS
from DataHandling.src.transcriber.utils import init_transcriber
from src.prompts import WELCOME_PROMPT, DEFAULT_ANSWER


class CustomerChatUI:
    def __init__(self):
        self.welcome_prompt = WELCOME_PROMPT
        self.default_answer = DEFAULT_ANSWER
        self.transcriber = init_transcriber()
        self.started = False
        self.completed_field_count = 0

        # Create a temporary directory for audio files
        self.temp_dir = tempfile.mkdtemp()
        print(f"Created temporary directory for audio files: {self.temp_dir}")

    def generate_tts(self, text):
        """Generate TTS and save to file"""
        # Create a unique filename
        unique_id = str(int(time.time()))
        temp_file = os.path.join(self.temp_dir, f"audio_{unique_id}.mp3")

        try:
            # Generate TTS
            tts = gTTS(text=text, lang="en")
            tts.save(temp_file)

            # Wait for file to be written
            max_retries = 5
            for i in range(max_retries):
                time.sleep(0.5)  # Wait briefly
                if os.path.exists(temp_file) and os.path.getsize(temp_file) > 0:
                    print(
                        f"TTS file created: {temp_file} ({os.path.getsize(temp_file)} bytes)"
                    )
                    return temp_file
                if i < max_retries - 1:
                    print(
                        f"Waiting for TTS file to be ready... (attempt {i+1}/{max_retries})"
                    )

            print("Failed to create TTS file after retries")
            return None
        except Exception as e:
            print(f"Error generating TTS: {str(e)}")
            return None

    def transcribe_audio(self, audio):
        """Transcribe audio data and return the text"""
        if not self.started:
            print("Interview hasn't started yet")
            return "Interview hasn't started yet"

        if audio is None:
            print("No audio received")
            return "No audio received"

        try:
            sr, y = audio

            # Check if audio is too short or empty
            if len(y) < 100 or np.max(np.abs(y)) < 0.01:
                print("Audio too short or silent")
                return "Silent audio detected"

            # Convert to mono if stereo
            if y.ndim > 1:
                y = y.mean(axis=1)

            # Normalize audio
            y = y.astype(np.float32)
            max_val = np.max(np.abs(y))
            if max_val > 0:
                y /= max_val

            # Transcribe
            text = self.transcriber({"sampling_rate": sr, "raw": y})["text"]

            # Return empty string if transcription is too short
            if not text or len(text.strip()) < 2:
                return "Inaudible speech detected"

            return text
        except Exception as e:
            print(f"Error transcribing audio: {str(e)}")
            return f"Error: {str(e)}"

    def progress_monitor(self):
        """Update the progress bar"""
        self.completed_field_count += random.randint(2, 4)
        if self.completed_field_count > 100:
            self.completed_field_count = 100
        current_progress = self.completed_field_count / 100
        print(f"Progress updated: {current_progress:.0%}")
        return current_progress

    def chat(self):
        """Create the chat interface"""
        with gr.Blocks(
            title="Medical Interview Assistant",
            css="""
            #start-button {
                background-color: #4CAF50;
                color: white;
                padding: 15px 32px;
                text-align: center;
                font-size: 18px;
                margin: 20px auto;
                display: block;
                width: 250px;
                border-radius: 10px;
                transition: all 0.3s;
            }
            #start-button:hover {
                background-color: #45a049;
                box-shadow: 0 4px 8px rgba(0,0,0,0.2);
            }
            #record-button {
                margin: 10px auto;
                max-width: 500px;
            }
            #progress-bar {
                margin: 20px auto;
                max-width: 500px;
            }
            #agent-audio {
                margin: 20px auto;
                max-width: 500px;
            }
        """,
        ) as demo:
            # Layout components
            with gr.Row():
                start_button = gr.Button(
                    "Start Interview",
                    variant="primary",
                    size="lg",
                    elem_id="start-button",
                )

            with gr.Row():
                record_button = gr.Audio(
                    sources=["microphone"],
                    type="numpy",
                    label="Record",
                    elem_id="record-button",
                    interactive=False,  # Initially disabled
                )

            with gr.Row():
                progress_bar = gr.Slider(
                    minimum=0,
                    maximum=1,
                    value=0,
                    label="Interview Progress",
                    elem_id="progress-bar",
                )

            with gr.Row():
                response_audio = gr.Audio(
                    label="Medical Assistant",
                    elem_id="agent-audio",
                    autoplay=True,
                )

            # Define the start button callback
            def start_interview():
                self.started = True
                audio_file = self.generate_tts(self.welcome_prompt)
                return audio_file, gr.update(visible=False), gr.update(interactive=True)

            start_button.click(
                fn=start_interview,
                inputs=[],
                outputs=[response_audio, start_button, record_button],
            )

            # Define the record button callback
            def handle_recording(audio):
                if not self.started or audio is None:
                    return None, 0

                # Transcribe audio
                text = self.transcribe_audio(audio)
                print(f"User said: {text}")

                # Generate response
                response_file = self.generate_tts(self.default_answer)
                print(f"Agent response: {self.default_answer}")

                # Update progress
                progress = self.progress_monitor()

                return response_file, progress

            record_button.change(
                fn=handle_recording,
                inputs=[record_button],
                outputs=[response_audio, progress_bar],
            )

        return demo
