# """
# FastAPI implementation for the Medical Interview System with WebSocket support.
# This allows real-time communication for the speech-based medical interview.
# """

# import os
# import warnings
# import json
# import asyncio
# import base64
# import numpy as np
# import io
# import wave
# from typing import Dict, List, Optional
# from fastapi import (
#     FastAPI,
#     WebSocket,
#     WebSocketDisconnect,
#     HTTPException,
#     Depends,
#     Request,
#     Response,
# )
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import JSONResponse
# from pydantic import BaseModel
# from starlette.websockets import WebSocketState
# import pyttsx3
# import threading

# # Local imports
# from src.llm.functionalities import HealthAgent
# from cli import VoiceActivityDetector, transcribe_audio
# import whisper
# import torch
# import logging

# # Configure logging
# logging.basicConfig(
#     level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
# )
# logger = logging.getLogger("medical_interview_api")

# # Disable TensorFlow warnings and set environment variables for performance optimizations
# os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
# os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
# warnings.filterwarnings("ignore", category=FutureWarning)
# warnings.filterwarnings("ignore", category=UserWarning)

# # Initialize FastAPI app
# app = FastAPI(
#     title="Medical Interview API",
#     description="API for conducting medical interviews with voice recognition and LLM processing",
#     version="1.0.0",
# )

# # Add CORS middleware with specific origins
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # Update with specific origins in production
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

# # Initialize TTS engine
# tts_engine = pyttsx3.init()


# # Initialize CUDA device and Whisper model at startup
# def get_cuda_device():
#     if torch.cuda.is_available():
#         device_id = torch.cuda.current_device()
#         device_name = torch.cuda.get_device_name(device_id)
#         cuda_device = f"cuda:{device_id}"
#         logger.info(f"Using {device_name} on {cuda_device}")
#         return cuda_device
#     else:
#         logger.info("CUDA is not available. Falling back to CPU.")
#         return "cpu"


# cuda_device = get_cuda_device()
# model = whisper.load_model(name="base", device=cuda_device)


# # Text-to-speech function running in a separate thread
# def speak_text_async(text):
#     """Convert text to speech in a separate thread."""
#     try:

#         def speak_worker():
#             tts_engine.say(text)
#             tts_engine.runAndWait()

#         thread = threading.Thread(target=speak_worker)
#         thread.daemon = True
#         thread.start()
#         return True
#     except Exception as e:
#         logger.error(f"TTS Error: {e}")
#         return False


# # Connection manager for WebSockets
# class ConnectionManager:
#     def __init__(self):
#         self.active_connections: Dict[str, WebSocket] = {}
#         self.interview_sessions: Dict[str, HealthAgent] = {}
#         self.vad_instances: Dict[str, VoiceActivityDetector] = {}
#         self.audio_buffers: Dict[str, List[bytes]] = {}
#         self.tts_threads: Dict[str, threading.Thread] = {}

#     async def connect(self, websocket: WebSocket, client_id: str):
#         try:
#             await websocket.accept()
#             self.active_connections[client_id] = websocket
#             self.interview_sessions[client_id] = HealthAgent()
#             self.vad_instances[client_id] = VoiceActivityDetector()
#             self.audio_buffers[client_id] = []
#             logger.info(f"Client {client_id} connected")
#             return True
#         except Exception as e:
#             logger.error(f"Error connecting client {client_id}: {e}")
#             return False

#     def disconnect(self, client_id: str):
#         try:
#             if client_id in self.active_connections:
#                 del self.active_connections[client_id]

#             if client_id in self.interview_sessions:
#                 try:
#                     self.interview_sessions[client_id].save_progress()
#                     logger.info(f"Progress saved for client {client_id}")
#                 except Exception as e:
#                     logger.error(f"Error saving progress for client {client_id}: {e}")
#                 del self.interview_sessions[client_id]

#             if client_id in self.vad_instances:
#                 del self.vad_instances[client_id]

#             if client_id in self.audio_buffers:
#                 del self.audio_buffers[client_id]

#             logger.info(f"Client {client_id} disconnected")
#         except Exception as e:
#             logger.error(f"Error during disconnect for client {client_id}: {e}")

#     async def send_message(self, client_id: str, message: str):
#         if client_id in self.active_connections:
#             try:
#                 # First generate the TTS in a separate thread
#                 speak_text_async(message)

#                 # Then send the text message over websocket
#                 await self.active_connections[client_id].send_text(
#                     json.dumps({"type": "text", "message": message})
#                 )
#                 logger.info(f"Message sent to client {client_id}")
#                 return True
#             except Exception as e:
#                 logger.error(f"Error sending message to client {client_id}: {e}")
#                 return False
#         return False

#     async def send_progress(self, client_id: str):
#         if client_id in self.interview_sessions:
#             try:
#                 agent = self.interview_sessions[client_id]

#                 # Calculate progress
#                 if not agent.form_sections:
#                     progress = 0
#                 else:
#                     try:
#                         current_index = agent.form_sections.index(agent.current_section)
#                         total_sections = len(agent.form_sections)
#                         progress = (current_index / total_sections) * 100

#                         if agent.missing_fields:
#                             total_fields = len(agent.form[agent.current_section])
#                             if total_fields > 0:
#                                 section_progress = (
#                                     total_fields - len(agent.missing_fields)
#                                 ) / total_fields
#                                 section_contribution = (
#                                     section_progress / total_sections
#                                 ) * 100
#                                 progress += section_contribution

#                         progress = min(progress, 99)
#                     except Exception as e:
#                         logger.error(f"Error calculating progress: {e}")
#                         progress = 0

#                 # Send progress update
#                 if client_id in self.active_connections:
#                     await self.active_connections[client_id].send_text(
#                         json.dumps(
#                             {
#                                 "type": "progress",
#                                 "progress": progress,
#                                 "current_section": agent.current_section,
#                             }
#                         )
#                     )
#                     return True
#             except Exception as e:
#                 logger.error(f"Error sending progress to client {client_id}: {e}")
#         return False

#     async def process_audio(self, client_id: str, audio_data: bytes):
#         """Process incoming audio chunk and add to buffer"""
#         if client_id not in self.vad_instances:
#             return

#         # Add audio chunk to buffer
#         self.audio_buffers[client_id].append(audio_data)
#         logger.debug(
#             f"Received audio chunk from client {client_id}, size: {len(audio_data)} bytes"
#         )

#     async def finish_audio(self, client_id: str):
#         """Process accumulated audio after speech is complete"""
#         if client_id not in self.audio_buffers or not self.audio_buffers[client_id]:
#             await self.send_message(client_id, "No audio received.")
#             return

#         try:
#             # Combine audio chunks
#             audio_data = b"".join(self.audio_buffers[client_id])
#             audio_size = len(audio_data)
#             self.audio_buffers[client_id] = []  # Clear buffer
#             logger.info(
#                 f"Processing audio from client {client_id}, size: {audio_size} bytes"
#             )

#             # Check if the audio is WebM format
#             if audio_data.startswith(b"\x1a\x45\xdf\xa3") or audio_data.startswith(
#                 b"1f 8b"
#             ):
#                 # WebM format - convert to wave using ffmpeg if possible
#                 try:
#                     import subprocess

#                     # Save WebM to temp file
#                     temp_webm = f"temp_{client_id}.webm"
#                     with open(temp_webm, "wb") as f:
#                         f.write(audio_data)

#                     # Convert to WAV using ffmpeg
#                     temp_wav = f"temp_{client_id}.wav"
#                     subprocess.run(
#                         [
#                             "ffmpeg",
#                             "-i",
#                             temp_webm,
#                             "-ar",
#                             "16000",
#                             "-ac",
#                             "1",
#                             "-y",
#                             temp_wav,
#                         ],
#                         stdout=subprocess.PIPE,
#                         stderr=subprocess.PIPE,
#                     )

#                     # Read WAV file
#                     with open(temp_wav, "rb") as f:
#                         wav_data = f.read()

#                     # Clean up temp files
#                     os.remove(temp_webm)
#                     os.remove(temp_wav)

#                     # Convert WAV to numpy array
#                     with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
#                         frames = wav_file.readframes(wav_file.getnframes())
#                         audio_np = np.frombuffer(frames, dtype=np.int16)
#                 except Exception as e:
#                     logger.error(f"Error converting WebM audio: {e}")
#                     await self.send_message(
#                         client_id,
#                         "There was an error processing your audio. Please try again.",
#                     )
#                     return
#             else:
#                 # Assume it's raw PCM data
#                 audio_np = np.frombuffer(audio_data, dtype=np.int16)

#             logger.info(f"Converted audio to numpy array, shape: {audio_np.shape}")

#             if len(audio_np) < 800:  # Too short to be valid speech
#                 logger.warning(f"Audio too short ({len(audio_np)} samples), ignoring")
#                 await self.send_message(
#                     client_id, "I didn't hear anything. Please try again."
#                 )
#                 return

#             # Transcribe audio
#             user_text = transcribe_audio(audio_np, model=model)
#             logger.info(f"Transcription result: {user_text}")

#             if user_text:
#                 # Process user input with HealthAgent
#                 if client_id in self.interview_sessions:
#                     agent = self.interview_sessions[client_id]
#                     response = agent.main_processor(user_text)
#                     logger.info(f"Agent response: {response}")

#                     # Send response to client
#                     await self.send_message(client_id, response)

#                     # Send updated progress
#                     await self.send_progress(client_id)
#             else:
#                 await self.send_message(
#                     client_id,
#                     "I didn't hear anything clear enough to understand. Please try again.",
#                 )
#         except Exception as e:
#             logger.error(f"Error processing audio data: {e}", exc_info=True)
#             await self.send_message(
#                 client_id, "There was an error processing your audio. Please try again."
#             )


# # Models for API requests
# class InterviewSession(BaseModel):
#     client_id: str


# # Create connection manager
# manager = ConnectionManager()


# # API routes
# @app.get("/")
# async def root():
#     return {
#         "message": "Medical Interview API is running. Connect via WebSocket to '/ws/{client_id}'"
#     }


# @app.post("/start_interview")
# async def start_interview(session: InterviewSession):
#     """Create a new interview session"""
#     client_id = session.client_id

#     # Check if session already exists
#     if client_id in manager.interview_sessions:
#         return {"message": f"Interview session for {client_id} already exists"}

#     # Create new session
#     manager.interview_sessions[client_id] = HealthAgent()
#     manager.vad_instances[client_id] = VoiceActivityDetector()

#     return {"message": f"Interview session created for {client_id}"}


# @app.get("/interview_status/{client_id}")
# async def interview_status(client_id: str):
#     """Get the current status of an interview session"""
#     if client_id not in manager.interview_sessions:
#         raise HTTPException(
#             status_code=404, detail=f"Interview session for {client_id} not found"
#         )

#     agent = manager.interview_sessions[client_id]

#     return {
#         "client_id": client_id,
#         "current_section": agent.current_section,
#         "missing_fields": agent.missing_fields,
#         "form_sections": agent.form_sections,
#         "talk_mode": agent.talk_mode,
#     }


# @app.get("/interview_data/{client_id}")
# async def interview_data(client_id: str):
#     """Get the current data collected in the interview session"""
#     if client_id not in manager.interview_sessions:
#         raise HTTPException(
#             status_code=404, detail=f"Interview session for {client_id} not found"
#         )

#     agent = manager.interview_sessions[client_id]

#     return {"client_id": client_id, "form_data": agent.form}


# @app.post("/save_interview/{client_id}")
# async def save_interview(client_id: str):
#     """Save the current interview data"""
#     if client_id not in manager.interview_sessions:
#         raise HTTPException(
#             status_code=404, detail=f"Interview session for {client_id} not found"
#         )

#     try:
#         agent = manager.interview_sessions[client_id]
#         agent.save_progress()
#         return {"message": f"Interview data saved for {client_id}"}
#     except Exception as e:
#         logger.error(f"Error saving interview: {e}")
#         raise HTTPException(
#             status_code=500, detail=f"Error saving interview data: {str(e)}"
#         )


# @app.websocket("/ws/{client_id}")
# async def websocket_endpoint(websocket: WebSocket, client_id: str):
#     """WebSocket endpoint for real-time communication during the interview"""
#     connection_successful = await manager.connect(websocket, client_id)

#     if not connection_successful:
#         logger.error(f"Failed to establish WebSocket connection for client {client_id}")
#         return

#     logger.info(f"WebSocket connection established for client {client_id}")

#     try:
#         # Send welcome message
#         await manager.send_message(
#             client_id,
#             "Welcome to the Medical Interview System. I'll be asking you some questions about your health. You can speak naturally, and I'll process your responses.",
#         )

#         # Start the interview with the first question
#         if client_id in manager.interview_sessions:
#             agent = manager.interview_sessions[client_id]
#             first_question = agent.main_processor(
#                 ""
#             )  # Empty input triggers initial question
#             await manager.send_message(client_id, first_question)

#         # Handle incoming messages
#         while True:
#             try:
#                 message = await websocket.receive()

#                 # Check for text messages
#                 if "text" in message:
#                     try:
#                         text_data = message["text"]
#                         data = json.loads(text_data)

#                         if data["type"] == "command":
#                             if data["command"] == "end_speech":
#                                 # Client indicates end of speech - process the buffered audio
#                                 logger.info(
#                                     f"Client {client_id} sent end_speech command"
#                                 )
#                                 await manager.finish_audio(client_id)
#                             elif data["command"] == "exit":
#                                 # Save progress and close connection
#                                 logger.info(f"Client {client_id} sent exit command")
#                                 if client_id in manager.interview_sessions:
#                                     manager.interview_sessions[
#                                         client_id
#                                     ].save_progress()
#                                 await manager.send_message(
#                                     client_id,
#                                     "Interview saved. Thank you for your time.",
#                                 )
#                                 break
#                     except json.JSONDecodeError:
#                         logger.error(
#                             f"Invalid JSON received from client {client_id}: {text_data}"
#                         )
#                     except KeyError as e:
#                         logger.error(
#                             f"Missing key in message from client {client_id}: {e}"
#                         )

#                 # Check for binary messages (audio data)
#                 elif "bytes" in message:
#                     binary_data = message["bytes"]
#                     await manager.process_audio(client_id, binary_data)

#             except WebSocketDisconnect:
#                 logger.info(
#                     f"Client {client_id} disconnected with WebSocketDisconnect exception"
#                 )
#                 break
#             except Exception as e:
#                 logger.error(f"Error processing message from client {client_id}: {e}")
#                 if "Cannot call" in str(e) and "once a disconnect" in str(e):
#                     # This is just a normal disconnect situation
#                     logger.info(f"Client {client_id} disconnected (normal)")
#                     break
#                 if "code" in dir(e) and e.code == 1000:  # Normal closure
#                     logger.info(f"Client {client_id} disconnected (normal closure)")
#                     break

#     except Exception as e:
#         logger.error(f"Error in WebSocket connection for client {client_id}: {e}")
#     finally:
#         # Clean up
#         logger.info(f"Cleaning up resources for client {client_id}")
#         manager.disconnect(client_id)


# # Run the FastAPI app
# if __name__ == "__main__":
#     import uvicorn

#     uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


"""
FastAPI implementation for the Medical Interview System with WebSocket support.
This allows real-time communication for the speech-based medical interview.
"""

import os
import warnings
import json
import asyncio
import base64
import numpy as np
import io
import wave
import tempfile
from typing import Dict, List, Optional
from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    Depends,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.websockets import WebSocketState
import threading
import queue

# Local imports
from src.llm.functionalities import HealthAgent
from cli import VoiceActivityDetector, transcribe_audio
import whisper
import torch
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("medical_interview_api")

# Disable TensorFlow warnings and set environment variables for performance optimizations
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

# Initialize FastAPI app
app = FastAPI(
    title="Medical Interview API",
    description="API for conducting medical interviews with voice recognition and LLM processing",
    version="1.0.0",
)

# Add CORS middleware with specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update with specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Initialize CUDA device and Whisper model at startup
def get_cuda_device():
    if torch.cuda.is_available():
        device_id = torch.cuda.current_device()
        device_name = torch.cuda.get_device_name(device_id)
        cuda_device = f"cuda:{device_id}"
        logger.info(f"Using {device_name} on {cuda_device}")
        return cuda_device
    else:
        logger.info("CUDA is not available. Falling back to CPU.")
        return "cpu"


cuda_device = get_cuda_device()
model = whisper.load_model(name="base", device=cuda_device)


# Connection manager for WebSockets
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.interview_sessions: Dict[str, HealthAgent] = {}
        self.vad_instances: Dict[str, VoiceActivityDetector] = {}
        self.audio_buffers: Dict[str, List[bytes]] = {}

    async def connect(self, websocket: WebSocket, client_id: str):
        try:
            await websocket.accept()
            self.active_connections[client_id] = websocket
            self.interview_sessions[client_id] = HealthAgent()
            self.vad_instances[client_id] = VoiceActivityDetector()
            self.audio_buffers[client_id] = []
            logger.info(f"Client {client_id} connected")
            return True
        except Exception as e:
            logger.error(f"Error connecting client {client_id}: {e}")
            return False

    def disconnect(self, client_id: str):
        try:
            if client_id in self.active_connections:
                del self.active_connections[client_id]

            if client_id in self.interview_sessions:
                try:
                    self.interview_sessions[client_id].save_progress()
                    logger.info(f"Progress saved for client {client_id}")
                except Exception as e:
                    logger.error(f"Error saving progress for client {client_id}: {e}")
                del self.interview_sessions[client_id]

            if client_id in self.vad_instances:
                del self.vad_instances[client_id]

            if client_id in self.audio_buffers:
                del self.audio_buffers[client_id]

            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"Error during disconnect for client {client_id}: {e}")

    async def send_message(self, client_id: str, message: str):
        if client_id in self.active_connections:
            try:
                # We'll rely on client-side TTS instead of server-side to avoid threading issues
                await self.active_connections[client_id].send_text(
                    json.dumps({"type": "text", "message": message})
                )
                logger.info(f"Message sent to client {client_id}")
                return True
            except Exception as e:
                logger.error(f"Error sending message to client {client_id}: {e}")
                return False
        return False

    async def send_progress(self, client_id: str):
        if client_id in self.interview_sessions:
            try:
                agent = self.interview_sessions[client_id]

                # Calculate progress
                if not agent.form_sections:
                    progress = 0
                else:
                    try:
                        current_index = agent.form_sections.index(agent.current_section)
                        total_sections = len(agent.form_sections)
                        progress = (current_index / total_sections) * 100

                        if agent.missing_fields:
                            total_fields = len(agent.form[agent.current_section])
                            if total_fields > 0:
                                section_progress = (
                                    total_fields - len(agent.missing_fields)
                                ) / total_fields
                                section_contribution = (
                                    section_progress / total_sections
                                ) * 100
                                progress += section_contribution

                        progress = min(progress, 99)
                    except Exception as e:
                        logger.error(f"Error calculating progress: {e}")
                        progress = 0

                # Send progress update
                if client_id in self.active_connections:
                    await self.active_connections[client_id].send_text(
                        json.dumps(
                            {
                                "type": "progress",
                                "progress": progress,
                                "current_section": agent.current_section,
                            }
                        )
                    )
                    return True
            except Exception as e:
                logger.error(f"Error sending progress to client {client_id}: {e}")
        return False

    async def process_audio(self, client_id: str, audio_data: bytes):
        """Process incoming audio chunk and add to buffer"""
        if client_id not in self.vad_instances:
            return

        # Add audio chunk to buffer
        self.audio_buffers[client_id].append(audio_data)
        logger.debug(
            f"Received audio chunk from client {client_id}, size: {len(audio_data)} bytes"
        )

    async def finish_audio(self, client_id: str):
        """Process accumulated audio after speech is complete"""
        if client_id not in self.audio_buffers or not self.audio_buffers[client_id]:
            await self.send_message(client_id, "No audio received.")
            return

        try:
            # Combine audio chunks
            audio_data = b"".join(self.audio_buffers[client_id])
            audio_size = len(audio_data)
            self.audio_buffers[client_id] = []  # Clear buffer
            logger.info(
                f"Processing audio from client {client_id}, size: {audio_size} bytes"
            )

            # Convert to numpy array based on format
            try:
                # First try direct conversion
                audio_np = np.frombuffer(audio_data, dtype=np.int16)

                # If audio seems valid (not too short), use it
                if len(audio_np) > 800:
                    logger.info(
                        f"Using direct audio conversion, shape: {audio_np.shape}"
                    )
                else:
                    # Audio may be in WebM format, try saving to a temporary file and extracting with ffmpeg
                    try:
                        with tempfile.NamedTemporaryFile(
                            suffix=".webm", delete=False
                        ) as temp_file:
                            temp_path = temp_file.name
                            temp_file.write(audio_data)

                        # Try to use ffmpeg to convert if available
                        import subprocess

                        output_path = temp_path + ".wav"
                        subprocess.run(
                            [
                                "ffmpeg",
                                "-i",
                                temp_path,
                                "-ar",
                                "16000",
                                "-ac",
                                "1",
                                "-f",
                                "wav",
                                output_path,
                            ],
                            check=True,
                            capture_output=True,
                        )

                        # Read the wav file
                        with wave.open(output_path, "rb") as wav_file:
                            audio_np = np.frombuffer(
                                wav_file.readframes(wav_file.getnframes()),
                                dtype=np.int16,
                            )

                        logger.info(
                            f"Converted audio using ffmpeg, shape: {audio_np.shape}"
                        )

                        # Clean up temp files
                        os.unlink(temp_path)
                        os.unlink(output_path)
                    except Exception as conversion_error:
                        logger.warning(
                            f"Could not convert audio format: {conversion_error}"
                        )
                        # Fall back to original audio even if it's short
                        logger.info("Falling back to original audio data")
            except Exception as e:
                logger.error(f"Error converting audio data: {e}")
                await self.send_message(
                    client_id,
                    "There was an error processing your audio. Please try again.",
                )
                return

            if len(audio_np) < 800:  # Too short to be valid speech
                logger.warning(f"Audio too short ({len(audio_np)} samples), ignoring")
                await self.send_message(
                    client_id, "I didn't hear anything. Please try again."
                )
                return

            # Transcribe audio
            user_text = transcribe_audio(audio_np, model=model)
            logger.info(f"Transcription result: {user_text}")

            if user_text:
                # Process user input with HealthAgent
                if client_id in self.interview_sessions:
                    agent = self.interview_sessions[client_id]
                    response = agent.main_processor(user_text)
                    logger.info(f"Agent response: {response}")

                    # Send response to client (browser will handle TTS)
                    await self.send_message(client_id, response)

                    # Send updated progress
                    await self.send_progress(client_id)
            else:
                await self.send_message(
                    client_id,
                    "I didn't hear anything clear enough to understand. Please try again.",
                )
        except Exception as e:
            logger.error(f"Error processing audio data: {e}", exc_info=True)
            await self.send_message(
                client_id, "There was an error processing your audio. Please try again."
            )


# Models for API requests
class InterviewSession(BaseModel):
    client_id: str


# Create connection manager
manager = ConnectionManager()


# API routes
@app.get("/")
async def root():
    return {
        "message": "Medical Interview API is running. Connect via WebSocket to '/ws/{client_id}'"
    }


@app.post("/start_interview")
async def start_interview(session: InterviewSession):
    """Create a new interview session"""
    client_id = session.client_id

    # Check if session already exists
    if client_id in manager.interview_sessions:
        return {"message": f"Interview session for {client_id} already exists"}

    # Create new session
    manager.interview_sessions[client_id] = HealthAgent()
    manager.vad_instances[client_id] = VoiceActivityDetector()

    return {"message": f"Interview session created for {client_id}"}


@app.get("/interview_status/{client_id}")
async def interview_status(client_id: str):
    """Get the current status of an interview session"""
    if client_id not in manager.interview_sessions:
        raise HTTPException(
            status_code=404, detail=f"Interview session for {client_id} not found"
        )

    agent = manager.interview_sessions[client_id]

    return {
        "client_id": client_id,
        "current_section": agent.current_section,
        "missing_fields": agent.missing_fields,
        "form_sections": agent.form_sections,
        "talk_mode": agent.talk_mode,
    }


@app.get("/interview_data/{client_id}")
async def interview_data(client_id: str):
    """Get the current data collected in the interview session"""
    if client_id not in manager.interview_sessions:
        raise HTTPException(
            status_code=404, detail=f"Interview session for {client_id} not found"
        )

    agent = manager.interview_sessions[client_id]

    return {"client_id": client_id, "form_data": agent.form}


@app.post("/save_interview/{client_id}")
async def save_interview(client_id: str):
    """Save the current interview data"""
    if client_id not in manager.interview_sessions:
        raise HTTPException(
            status_code=404, detail=f"Interview session for {client_id} not found"
        )

    try:
        agent = manager.interview_sessions[client_id]
        agent.save_progress()
        return {"message": f"Interview data saved for {client_id}"}
    except Exception as e:
        logger.error(f"Error saving interview: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error saving interview data: {str(e)}"
        )


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """WebSocket endpoint for real-time communication during the interview"""
    connection_successful = await manager.connect(websocket, client_id)

    if not connection_successful:
        logger.error(f"Failed to establish WebSocket connection for client {client_id}")
        return

    logger.info(f"WebSocket connection established for client {client_id}")

    try:
        # Send welcome message
        await manager.send_message(
            client_id,
            "Welcome to the Medical Interview System. I'll be asking you some questions about your health. You can speak naturally, and I'll process your responses.",
        )

        # Start the interview with the first question
        if client_id in manager.interview_sessions:
            agent = manager.interview_sessions[client_id]
            first_question = agent.main_processor(
                ""
            )  # Empty input triggers initial question
            await manager.send_message(client_id, first_question)

        # Handle incoming messages
        while True:
            try:
                message = await websocket.receive()

                # Check for text messages
                if "text" in message:
                    try:
                        text_data = message["text"]
                        data = json.loads(text_data)

                        if data["type"] == "command":
                            if data["command"] == "end_speech":
                                # Client indicates end of speech - process the buffered audio
                                logger.info(
                                    f"Client {client_id} sent end_speech command"
                                )
                                await manager.finish_audio(client_id)
                            elif data["command"] == "exit":
                                # Save progress and close connection
                                logger.info(f"Client {client_id} sent exit command")
                                if client_id in manager.interview_sessions:
                                    manager.interview_sessions[
                                        client_id
                                    ].save_progress()
                                await manager.send_message(
                                    client_id,
                                    "Interview saved. Thank you for your time.",
                                )
                                break
                    except json.JSONDecodeError:
                        logger.error(
                            f"Invalid JSON received from client {client_id}: {text_data}"
                        )
                    except KeyError as e:
                        logger.error(
                            f"Missing key in message from client {client_id}: {e}"
                        )

                # Check for binary messages (audio data)
                elif "bytes" in message:
                    binary_data = message["bytes"]
                    await manager.process_audio(client_id, binary_data)

            except WebSocketDisconnect:
                logger.info(
                    f"Client {client_id} disconnected with WebSocketDisconnect exception"
                )
                break
            except Exception as e:
                logger.error(f"Error processing message from client {client_id}: {e}")
                if "Cannot call" in str(e) and "once a disconnect" in str(e):
                    # This is just a normal disconnect situation
                    logger.info(f"Client {client_id} disconnected (normal)")
                    break
                if "code" in dir(e) and e.code == 1000:  # Normal closure
                    logger.info(f"Client {client_id} disconnected (normal closure)")
                    break

    except Exception as e:
        logger.error(f"Error in WebSocket connection for client {client_id}: {e}")
    finally:
        # Clean up
        logger.info(f"Cleaning up resources for client {client_id}")
        manager.disconnect(client_id)


# Run the FastAPI app
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
