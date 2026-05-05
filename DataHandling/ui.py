import gradio as gr
import numpy as np
import os
import time
import sounddevice as sd
from threading import Thread

# Set TensorFlow environment variables
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

from src.audio.utils import init_transcriber, generate_tts
from src.llm.functionalities import HealthAgent


class CustomerChatUI(HealthAgent):
    """
    A customer chat interface using Gradio with audio recording and transcription.
    Uses LLM to generate responses and process medical interview information.
    """

    def __init__(self):
        # Initialize HealthAgent parent class
        super(CustomerChatUI, self).__init__()

        # Initialize audio transcription components
        self.transcriber = init_transcriber()
        self.started = False
        self.interview_accepted = False
        self.audio_recording = []
        self.is_recording = False
        self.fs = 44100  # Sample rate
        self.processing_lock = False  # Lock to prevent multiple simultaneous processing

        # Progress tracking for UI
        self.completed_field_count = 0
        self.chatbox_messages = []

    def transcribe_audio(self, audio_data):
        """
        Transcribe audio data and return the text.
        """
        if not self.started:
            return "Interview hasn't started yet"

        if audio_data is None or len(audio_data) == 0:
            return "No audio received"

        try:
            y = np.concatenate(audio_data, axis=0).astype(np.float32)
            if y.ndim > 1:
                y = y.mean(axis=1)  # Convert to mono

            max_val = np.max(np.abs(y))
            if max_val > 0:
                y /= max_val  # Normalize

            text = self.transcriber({"sampling_rate": self.fs, "raw": y})["text"]
            print("Transcribed: ", text)

            if text and len(text.strip()) > 2:
                # Add user message to history
                self.history.append({"role": "user", "message": text})
                return text
            else:
                return "Inaudible speech detected"

        except Exception as e:
            print(f"Error in transcription: {str(e)}")
            return f"Error: {str(e)}"

    def record_audio(self):
        """
        Record audio in a background thread.
        """
        self.audio_recording = []
        with sd.InputStream(
            samplerate=self.fs, channels=1, callback=self.audio_callback
        ):
            while self.is_recording:
                sd.sleep(100)

    def audio_callback(self, indata, frames, time, status):
        """
        Audio callback function to record.
        """
        if status:
            print(f"Audio callback status: {status}")
        self.audio_recording.append(indata.copy())

    def toggle_recording(self):
        """
        Start or stop recording.
        """
        if not self.is_recording:
            if self.processing_lock:
                print("Still processing previous recording, please wait...")
                return "Please wait..."

            self.is_recording = True
            self.audio_recording = []  # Explicitly clear the buffer
            Thread(target=self.record_audio, daemon=True).start()
            return "Recording... Press to Stop"
        else:
            self.is_recording = False
            return "Start Recording"

    def progress_monitor(self):
        """
        Update the progress bar based on form completion status.
        """
        if not self.form_sections:
            return 0

        # Calculate progress based on current section index and missing fields
        try:
            current_index = self.form_sections.index(self.current_section)
            total_sections = len(self.form_sections)

            # Base progress is how far we've advanced through sections
            base_progress = current_index / total_sections

            # Additional progress within current section
            section_progress = 0
            if self.missing_fields:
                # If there are missing fields, we're partially through the section
                total_fields = len(self.form[self.current_section])
                if total_fields > 0:
                    section_progress = (total_fields - len(self.missing_fields)) / (
                        total_fields * 2
                    )
            else:
                # If no missing fields, we're completely through this section
                section_progress = 0.5

            # Combine for overall progress
            progress = base_progress + (section_progress / total_sections)
            return min(progress, 0.99)  # Cap at 99% until fully complete
        except Exception as e:
            print(f"Error calculating progress: {e}")
            return 0

    def process_user_response(self, text):
        """
        Process the user's response and get the next question.
        """
        try:
            if not self.interview_accepted and not self.check_if_interview_accepted(
                text
            ):
                self.interview_accepted = False
                return self.interview_declined_message

            if not self.interview_accepted:
                self.interview_accepted = True

            # Process the user input and get the next response
            response_text = self.main_processor(text)

            # Add the assistant's response to history
            self.history.append({"role": "agent", "message": response_text})

            return response_text
        except Exception as e:
            print(f"Error processing user response: {e}")
            return "I apologize for the technical difficulty. Could you please repeat your answer?"

    def check_if_interview_accepted(self, text):
        """Check if the user has agreed to start the interview."""
        text = text.lower()
        positive_responses = [
            "yes",
            "yeah",
            "sure",
            "okay",
            "ok",
            "fine",
            "alright",
            "ready",
            "let's start",
            "let's begin",
            "start",
            "begin",
            "please",
        ]

        for response in positive_responses:
            if response in text:
                return True

        return False

    def ensure_string(self, text):
        """Ensure the text is a string and not some other object"""
        if hasattr(text, "text"):  # Some API responses might have a .text attribute
            return text.text
        elif hasattr(text, "__str__"):  # Convert to string if possible
            return str(text)
        else:
            return "Response could not be processed"

    def chat(self):
        """
        Create the chat interface.
        """
        # Start the keyword-checking thread
        keyword_thread = Thread(target=self.check_for_keyword, daemon=True)
        keyword_thread.start()

        with gr.Blocks(title="Medical Interview Assistant") as demo:
            # Create UI components
            with gr.Row():
                with gr.Column(scale=3):
                    chatbox = gr.Chatbot(label="Conversation", height=500)

                with gr.Column(scale=1):
                    progress_bar = gr.Slider(
                        minimum=0, maximum=1, value=0, label="Interview Progress"
                    )
                    form_status = gr.JSON(label="Current Form Data", visible=True)

            with gr.Row():
                start_button = gr.Button(
                    "Start Interview", variant="primary", size="lg"
                )
                record_button = gr.Button("Start Recording", visible=False)

            response_audio = gr.Audio(label="Medical Assistant", autoplay=True)

            def start_interview():
                """Initialize the interview and play welcome message."""
                try:
                    self.started = True
                    self.interview_accepted = False

                    # Set welcome message
                    initial_message = self.ready_to_start_message

                    # Add to history
                    self.history = []  # Reset history
                    self.history.append({"role": "agent", "message": initial_message})

                    # Update chatbox
                    chatbox_msgs = []
                    chatbox_msgs.append((None, initial_message))

                    # Generate audio for welcome message
                    try:
                        # Ensure message is a string
                        message_text = self.ensure_string(initial_message)
                        audio_file = generate_tts(text=message_text)
                    except Exception as e:
                        print(f"Error generating TTS: {e}")
                        audio_file = None

                    # Reset form state
                    self.init_form()

                    return (
                        chatbox_msgs,
                        audio_file,
                        gr.update(visible=False),
                        gr.update(visible=True),
                        self.form,
                    )
                except Exception as e:
                    print(f"Error starting interview: {e}")
                    # Emergency fallback
                    return (
                        [
                            (
                                "System",
                                "Error starting interview. Please refresh the page.",
                            )
                        ],
                        None,
                        gr.update(visible=True),
                        gr.update(visible=False),
                        {},
                    )

            def handle_recording():
                """Handle recording toggle and process transcribed audio."""
                try:
                    button_text = self.toggle_recording()

                    if not self.is_recording:
                        # Set processing lock
                        self.processing_lock = True

                        # Get transcribed text
                        text = self.transcribe_audio(self.audio_recording)

                        if (
                            text
                            and text != "Inaudible speech detected"
                            and text != "No audio received"
                        ):
                            print(f"Processing user response: {text}")

                            # Start with current messages if available
                            current_messages = chatbox.value if chatbox.value else []

                            # Add user message
                            current_messages.append((text, None))

                            try:
                                # Process user response and get next question
                                response_text = self.process_user_response(text)
                                print(f"Assistant response: {response_text}")

                                # Ensure response_text is a string
                                response_text = self.ensure_string(response_text)

                                # Add assistant response
                                current_messages.append((None, response_text))

                                # Generate audio for assistant response
                                try:
                                    response_file = generate_tts(text=response_text)
                                except Exception as e:
                                    print(f"Error generating TTS: {e}")
                                    response_file = None

                                # Update progress
                                progress = self.progress_monitor()

                                # Release processing lock with delay
                                time.sleep(0.5)
                                self.processing_lock = False

                                return (
                                    button_text,
                                    current_messages,
                                    response_file,
                                    progress,
                                    self.form,
                                )
                            except Exception as e:
                                print(f"Error in response processing: {e}")
                                error_msg = "I'm sorry, I encountered an error. Let's try again."
                                current_messages.append((None, error_msg))
                                self.processing_lock = False
                                return (
                                    button_text,
                                    current_messages,
                                    None,
                                    None,
                                    self.form,
                                )

                        # Release lock if no valid transcription
                        self.processing_lock = False
                        # If no valid transcription, just return button state and current chatbox
                        return button_text, chatbox.value, None, None, None

                    # If still recording, just return button state and current chatbox
                    return button_text, chatbox.value, None, None, None
                except Exception as e:
                    print(f"Critical error in handle_recording: {e}")
                    self.processing_lock = False
                    # Emergency fallback
                    return "Start Recording", chatbox.value, None, None, None

            # Connect UI event handlers
            start_button.click(
                start_interview,
                inputs=[],
                outputs=[
                    chatbox,
                    response_audio,
                    start_button,
                    record_button,
                    form_status,
                ],
            )

            record_button.click(
                handle_recording,
                inputs=[],
                outputs=[
                    record_button,
                    chatbox,
                    response_audio,
                    progress_bar,
                    form_status,
                ],
            )

        return demo

