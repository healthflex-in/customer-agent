# import asyncio
# import websockets
# import pyaudio
# import webrtcvad
# import wave
# import time
# import numpy as np
# import base64
# import json
# import io
# from pydub import AudioSegment
# from pydub.playback import play

# # Audio Configuration
# FORMAT = pyaudio.paInt16
# CHANNELS = 1
# RATE = 16000  # Whisper expects 16kHz
# FRAME_DURATION_MS = 30
# CHUNK = int(RATE * (FRAME_DURATION_MS / 1000))  # 30ms frame size

# # Speech Detection Settings
# VAD_AGGRESSIVENESS = 3
# MIN_SPEECH_DURATION_SEC = 0.5  # Minimum speech duration to start recording
# SILENCE_THRESHOLD_SEC = 2.0  # Wait for 2 seconds of silence before sending
# MAX_RECORDING_DURATION_SEC = 30.0  # Maximum recording time before forced send

# # Initialize VAD
# vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)


# # Function to save raw recorded audio before sending
# def save_audio(file_name, audio_data):
#     with wave.open(file_name, "wb") as wf:
#         wf.setnchannels(CHANNELS)
#         wf.setsampwidth(2)  # 16-bit PCM
#         wf.setframerate(RATE)
#         wf.writeframes(audio_data)


# def play_audio_bytes(audio_bytes):
#     """Play audio bytes using pydub"""
#     try:
#         # Convert bytes to WAV audio segment
#         audio = AudioSegment.from_wav(io.BytesIO(audio_bytes))
#         # Play the audio
#         play(audio)
#         return True
#     except Exception as e:
#         print(f"Error playing audio: {e}")
#         return False


# def play_base64_audio(base64_audio):
#     """Convert base64 audio to bytes and play it"""
#     try:
#         audio_bytes = base64.b64decode(base64_audio)
#         return play_audio_bytes(audio_bytes)
#     except Exception as e:
#         print(f"Error decoding/playing base64 audio: {e}")
#         return False


# async def record_audio():
#     """Record audio until silence is detected or max duration reached"""
#     p = pyaudio.PyAudio()
#     stream = p.open(
#         format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK
#     )

#     audio_buffer = bytearray()
#     is_recording = False
#     silent_frames = 0
#     speaking_frames = 0
#     required_silent_frames = int(SILENCE_THRESHOLD_SEC * 1000 / FRAME_DURATION_MS)
#     min_speech_frames = int(MIN_SPEECH_DURATION_SEC * 1000 / FRAME_DURATION_MS)
#     max_recording_frames = int(MAX_RECORDING_DURATION_SEC * 1000 / FRAME_DURATION_MS)

#     total_frames = 0
#     recording_start_time = None

#     print("Listening... (Will wait for speech and then silence before sending)")

#     try:
#         while True:
#             # Read audio chunk
#             data = stream.read(CHUNK, exception_on_overflow=False)

#             # Check if frame contains speech
#             try:
#                 is_speech = vad.is_speech(data, RATE)
#             except Exception as e:
#                 print(f"VAD error: {e}, assuming speech")
#                 is_speech = True

#             # Add data to buffer regardless of speech status
#             audio_buffer.extend(data)

#             # Update frame counters
#             if is_speech:
#                 speaking_frames += 1
#                 silent_frames = 0

#                 # Start recording if we have enough consecutive speech frames
#                 if not is_recording and speaking_frames >= min_speech_frames:
#                     is_recording = True
#                     recording_start_time = time.time()
#                     total_frames = 0
#                     print("🎤 Speech detected - Recording started")
#             else:
#                 silent_frames += 1
#                 speaking_frames = 0

#             # If we're recording, increment total frame count
#             if is_recording:
#                 total_frames += 1

#             # Determine if we should finish recording based on three conditions:
#             # 1. We're recording and detected enough silence to indicate end of speech
#             # 2. We're recording and reached maximum duration
#             # 3. We have a significant buffer but no speech detected (cleanup)

#             recording_duration = 0
#             if recording_start_time:
#                 recording_duration = time.time() - recording_start_time

#             should_finish = False
#             finish_reason = ""

#             if is_recording and silent_frames >= required_silent_frames:
#                 should_finish = True
#                 finish_reason = f"Silence threshold reached ({SILENCE_THRESHOLD_SEC}s)"
#             elif is_recording and total_frames >= max_recording_frames:
#                 should_finish = True
#                 finish_reason = f"Maximum recording duration reached ({MAX_RECORDING_DURATION_SEC}s)"
#             elif (
#                 len(audio_buffer) > RATE * 4 * 2 and not is_recording
#             ):  # 4 seconds of non-speech data
#                 # Clear buffer without finishing if we've accumulated non-speech
#                 print("Clearing unused audio buffer (no speech detected)")
#                 audio_buffer = bytearray()

#             if should_finish:
#                 buffer_duration = len(audio_buffer) / (RATE * 2)  # 2 bytes per sample

#                 print(f"⏹️ Recording finished: {finish_reason}")
#                 print(
#                     f"📤 Sending {buffer_duration:.2f}s of audio ({len(audio_buffer)} bytes)"
#                 )

#                 # Save audio for debugging
#                 timestamp = int(time.time())
#                 filename = f"client_audio_{timestamp}.wav"
#                 save_audio(filename, bytes(audio_buffer))
#                 print(f"💾 Audio saved to {filename}")

#                 result = bytes(audio_buffer)
#                 stream.stop_stream()
#                 stream.close()
#                 p.terminate()
#                 return result

#             # Add a small sleep to avoid CPU hogging
#             await asyncio.sleep(0.001)

#     except Exception as e:
#         print(f"Error recording audio: {e}")
#         stream.stop_stream()
#         stream.close()
#         p.terminate()
#         return None


# async def send_audio():
#     uri = "ws://localhost:8000/ws"

#     try:
#         async with websockets.connect(uri) as websocket:
#             print("Connected to WebSocket Server")

#             # Wait for welcome message from server
#             print("Waiting for welcome message...")
#             welcome_msg = await websocket.recv()

#             try:
#                 # Parse the welcome message
#                 welcome_data = json.loads(welcome_msg)

#                 if welcome_data.get("type") == "welcome_audio":
#                     # Play the welcome audio
#                     print(f"Received welcome message: {welcome_data.get('text')}")
#                     audio_base64 = welcome_data.get("audio")
#                     if audio_base64:
#                         print("Playing welcome audio...")
#                         play_base64_audio(audio_base64)
#                 else:
#                     print(f"Unexpected message type: {welcome_data.get('type')}")
#             except json.JSONDecodeError:
#                 print("Error decoding welcome message")
#             except Exception as e:
#                 print(f"Error processing welcome: {e}")

#             # Main conversation loop
#             while True:
#                 print("\nListening for your speech...")

#                 # Record audio with VAD
#                 audio_data = await record_audio()

#                 if not audio_data or len(audio_data) < RATE:  # Less than 1 second
#                     print("No valid audio recorded, please try again")
#                     continue

#                 # Send recorded audio to server
#                 await websocket.send(audio_data)
#                 print("Audio sent to server, waiting for response...")

#                 # Receive response from server
#                 response = await websocket.recv()

#                 try:
#                     response_data = json.loads(response)
#                     msg_type = response_data.get("type")

#                     if msg_type == "response_audio":
#                         # Play the response audio
#                         text = response_data.get("text", "")
#                         print(f"Server response: {text}")

#                         audio_base64 = response_data.get("audio")
#                         if audio_base64:
#                             print("Playing response audio...")
#                             play_base64_audio(audio_base64)
#                     elif msg_type == "error":
#                         print(f"Error from server: {response_data.get('text')}")
#                     else:
#                         print(f"Received: {response}")

#                 except json.JSONDecodeError:
#                     print(f"Error decoding server response: {response}")
#                 except Exception as e:
#                     print(f"Error processing response: {e}")

#     except KeyboardInterrupt:
#         print("Stopping audio stream...")
#     except Exception as e:
#         print(f"WebSocket error: {e}")


# if __name__ == "__main__":
#     asyncio.run(send_audio())


import asyncio
import websockets
import pyaudio
import webrtcvad
import wave
import time
import numpy as np
import base64
import json
import io
from pydub import AudioSegment
from pydub.playback import play
import os

# Audio Configuration
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000  # Whisper expects 16kHz
FRAME_DURATION_MS = 30
CHUNK = int(RATE * (FRAME_DURATION_MS / 1000))  # 30ms frame size

# Speech Detection Settings
VAD_AGGRESSIVENESS = 3
MIN_SPEECH_DURATION_SEC = 0.5  # Minimum speech duration to start recording
SILENCE_THRESHOLD_SEC = 2.0  # Wait for 2 seconds of silence before sending
MAX_RECORDING_DURATION_SEC = 30.0  # Maximum recording time before forced send

# Real-time streaming settings
STREAMING_INTERVAL = 0.5  # Stream every 0.5 seconds

# Create directories
os.makedirs("received_audio", exist_ok=True)

# Initialize VAD
vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)


# Function to save raw recorded audio before sending
def save_audio(file_name, audio_data):
    with wave.open(file_name, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)  # 16-bit PCM
        wf.setframerate(RATE)
        wf.writeframes(audio_data)


def play_audio_bytes(audio_bytes):
    """Play audio bytes using pydub and block until complete"""
    try:
        # Convert bytes to WAV audio segment
        audio = AudioSegment.from_wav(io.BytesIO(audio_bytes))
        # Play the audio (this blocks until audio finishes)
        play(audio)
        # Add a small delay after playback completes
        time.sleep(0.5)
        return True
    except Exception as e:
        print(f"Error playing audio: {e}")
        return False


def display_interview_state(interview_state):
    """Display interview state information in a formatted way"""
    if not interview_state:
        return

    section = interview_state.get("section", "Starting")
    progress = interview_state.get("progress", 0)
    missing_fields = interview_state.get("missing_fields", [])

    # Create a progress bar
    bar_length = 30
    filled_length = int(bar_length * progress / 100)
    bar = "█" * filled_length + "░" * (bar_length - filled_length)

    print(f"\n📋 INTERVIEW PROGRESS")
    print(f"   Current section: {section}")
    print(f"   Progress: |{bar}| {progress:.1f}%")
    if missing_fields:
        print(f"   Missing information: {', '.join(missing_fields)}")
    print("-" * 50)


async def stream_audio_recording(websocket):
    """Record and stream audio in real-time"""
    p = pyaudio.PyAudio()
    stream = p.open(
        format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK
    )

    audio_buffer = bytearray()
    is_recording = False
    silent_frames = 0
    speaking_frames = 0
    required_silent_frames = int(SILENCE_THRESHOLD_SEC * 1000 / FRAME_DURATION_MS)
    min_speech_frames = int(MIN_SPEECH_DURATION_SEC * 1000 / FRAME_DURATION_MS)
    max_recording_frames = int(MAX_RECORDING_DURATION_SEC * 1000 / FRAME_DURATION_MS)

    total_frames = 0
    recording_start_time = None
    last_stream_time = 0
    streaming_started = False
    full_audio_buffer = bytearray()  # For saving the complete recording

    print("Listening... (Will wait for speech and then silence before sending)")

    try:
        while True:
            # Read audio chunk
            data = stream.read(CHUNK, exception_on_overflow=False)

            # Check if frame contains speech
            try:
                is_speech = vad.is_speech(data, RATE)
            except Exception as e:
                print(f"VAD error: {e}, assuming speech")
                is_speech = True

            # Add data to buffer
            audio_buffer.extend(data)
            if is_recording:
                full_audio_buffer.extend(data)  # Keep a complete copy for saving

            # Update frame counters
            if is_speech:
                speaking_frames += 1
                silent_frames = 0

                # Start recording if we have enough consecutive speech frames
                if not is_recording and speaking_frames >= min_speech_frames:
                    is_recording = True
                    recording_start_time = time.time()
                    last_stream_time = recording_start_time
                    streaming_started = False
                    total_frames = 0
                    full_audio_buffer = bytearray()  # Reset full audio buffer

                    # Signal recording start to server
                    await websocket.send(
                        json.dumps(
                            {"type": "audio_start", "timestamp": recording_start_time}
                        )
                    )

                    print("🎤 Speech detected - Recording started")
            else:
                silent_frames += 1
                speaking_frames = 0

            # If we're recording, increment total frame count
            if is_recording:
                total_frames += 1

                # Check if it's time to stream accumulated audio
                current_time = time.time()
                if current_time - last_stream_time >= STREAMING_INTERVAL:
                    # Stream accumulated audio since last stream
                    if not streaming_started:
                        streaming_started = True
                        print("🔄 Streaming audio in real-time...")

                    # Send current buffer
                    await websocket.send(bytes(audio_buffer))

                    # Reset buffer and update last stream time
                    audio_buffer = bytearray()
                    last_stream_time = current_time

            # Determine if we should finish recording based on:
            # 1. Detected enough silence to indicate end of speech
            # 2. Reached maximum duration
            # 3. Significant buffer but no speech detected (cleanup)

            recording_duration = 0
            if recording_start_time:
                recording_duration = time.time() - recording_start_time

            should_finish = False
            finish_reason = ""

            if is_recording and silent_frames >= required_silent_frames:
                should_finish = True
                finish_reason = f"Silence threshold reached ({SILENCE_THRESHOLD_SEC}s)"
            elif is_recording and total_frames >= max_recording_frames:
                should_finish = True
                finish_reason = f"Maximum recording duration reached ({MAX_RECORDING_DURATION_SEC}s)"
            elif (
                len(audio_buffer) > RATE * 4 * 2 and not is_recording
            ):  # 4 seconds of non-speech data
                # Clear buffer without finishing if we've accumulated non-speech
                print("Clearing unused audio buffer (no speech detected)")
                audio_buffer = bytearray()

            if should_finish and is_recording:
                buffer_duration = len(audio_buffer) / (RATE * 2)  # 2 bytes per sample

                print(f"⏹️ Recording finished: {finish_reason}")

                # Send any remaining audio in buffer
                if len(audio_buffer) > 0:
                    await websocket.send(bytes(audio_buffer))

                # Signal end of recording
                await websocket.send(
                    json.dumps(
                        {
                            "type": "audio_end",
                            "duration": recording_duration,
                            "timestamp": time.time(),
                        }
                    )
                )

                print("⏳ Waiting for server to process audio...")

                # Save complete audio for debugging
                audio_size_mb = len(full_audio_buffer) / (1024 * 1024)
                timestamp = int(time.time())
                filename = f"client_audio_{timestamp}.wav"

                if audio_size_mb < 10:  # Only save if less than 10MB
                    save_audio(filename, bytes(full_audio_buffer))
                    print(
                        f"💾 Full recording saved to {filename} ({audio_size_mb:.2f} MB)"
                    )
                else:
                    print(f"⚠️ Audio too large to save ({audio_size_mb:.2f} MB)")

                # Reset for next recording
                audio_buffer = bytearray()
                is_recording = False
                silent_frames = 0
                speaking_frames = 0
                recording_start_time = None
                streaming_started = False

                # Return to indicate we're done with this recording
                break

            # Add a small sleep to avoid CPU hogging
            await asyncio.sleep(0.001)

    except Exception as e:
        print(f"Error recording audio: {e}")
        # Try to signal recording end if there was an error
        if is_recording:
            try:
                await websocket.send(json.dumps({"type": "audio_end", "error": str(e)}))
            except:
                pass
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()


class AudioReceiver:
    """Class to handle and track audio from server"""

    def __init__(self):
        self.audio_buffers = {}  # message_id -> buffer
        self.last_text_message = None
        self.last_interview_state = None
        self.audio_playing = False
        self.audio_complete_event = asyncio.Event()

    async def handle_message(self, message):
        """Process messages from server"""
        try:
            # Make sure we're dealing with a string (text message)
            if not isinstance(message, str):
                print(f"Received non-text message: {type(message)}")
                return False

            data = json.loads(message)
            msg_type = data.get("type", "")

            # Handle text message
            if msg_type == "text_message":
                text = data.get("text", "")
                print(f"\n🤖 AI: {text}")
                self.last_text_message = text

                # Display interview state information
                interview_state = data.get("interview_state", {})
                self.last_interview_state = interview_state
                display_interview_state(interview_state)

                # Text messages do not complete the response
                return False

            # Handle audio start message
            elif msg_type == "audio_start":
                message_id = data.get("message_id", "unknown")
                print(f"🔊 Server started sending audio: {message_id}")
                self.audio_buffers[message_id] = bytearray()
                self.audio_playing = True
                self.audio_complete_event.clear()
                return False

            # Handle audio chunk
            elif msg_type == "audio_chunk":
                message_id = data.get("message_id", "unknown")
                is_last = data.get("is_last", False)
                audio_chunk_b64 = data.get("data", "")

                if message_id in self.audio_buffers:
                    # Decode and add to buffer
                    audio_chunk = base64.b64decode(audio_chunk_b64)
                    self.audio_buffers[message_id].extend(audio_chunk)

                    if is_last:
                        # Complete audio received, save and play
                        full_audio = bytes(self.audio_buffers[message_id])
                        timestamp = int(time.time())
                        filename = f"received_audio/server_audio_{timestamp}.wav"
                        save_audio(filename, full_audio)
                        print(f"✅ Audio complete, playing ({len(full_audio)} bytes)")

                        # Play the audio (this blocks until complete)
                        play_audio_bytes(full_audio)

                        # Clean up buffer and signal audio completion
                        del self.audio_buffers[message_id]
                        self.audio_playing = False
                        self.audio_complete_event.set()

                        # Audio complete signals end of complete response
                        return True

                return False

            # Handle error
            elif msg_type == "error":
                print(f"⚠️ Error from server: {data.get('text', 'Unknown error')}")
                return False

        except json.JSONDecodeError:
            print(f"Error parsing message: {message[:100]}...")
        except Exception as e:
            print(f"Error handling server message: {e}")

        return False


async def send_audio():
    uri = "ws://localhost:8000/ws"

    audio_receiver = AudioReceiver()

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to WebSocket Server")

            # Wait for the welcome message sequence
            welcome_complete = False
            while not welcome_complete:
                message = await websocket.recv()
                welcome_complete = await audio_receiver.handle_message(message)

            # Main conversation loop - runs properly sequentially
            while True:
                # Only start listening after audio has finished playing
                if audio_receiver.audio_playing:
                    await audio_receiver.audio_complete_event.wait()

                # Now start listening for user input
                print("\n🎤 Listening for your response... (speak when ready)")
                await stream_audio_recording(websocket)

                # Process server responses until audio completes
                response_complete = False
                while not response_complete:
                    try:
                        message = await websocket.recv()
                        response_complete = await audio_receiver.handle_message(message)
                    except Exception as e:
                        print(f"Error receiving server response: {e}")
                        response_complete = True  # Break out on error

    except KeyboardInterrupt:
        print("Stopping audio stream...")
    except Exception as e:
        print(f"WebSocket error: {e}")


if __name__ == "__main__":
    asyncio.run(send_audio())
