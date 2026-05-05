# from fastapi import FastAPI, WebSocket
# import uvicorn
# import asyncio
# import numpy as np
# import whisper
# import torch
# import wave
# import time
# import os
# import base64
# import io
# import json
# from pathlib import Path
# import gtts  # Google Text-to-Speech
# from pydub import AudioSegment  # For audio conversion

# # Import HealthAgent and related functionalities
# from src.llm.functionalities import HealthAgent

# app = FastAPI()

# # Create directories for saving audio and transcripts
# os.makedirs("audio_files", exist_ok=True)
# os.makedirs("tts_cache", exist_ok=True)
# os.makedirs("transcripts", exist_ok=True)

# # Load Whisper model
# print("Loading Whisper model...")
# cuda_device = "cuda" if torch.cuda.is_available() else "cpu"
# model = whisper.load_model("base", device=cuda_device)
# print(f"Model loaded on {cuda_device}")

# # Initialize HealthAgent
# print("Initializing HealthAgent...")
# health_agent = HealthAgent()
# print("HealthAgent initialized")

# # Audio parameters
# RATE = 16000  # 16kHz
# SAMPLE_WIDTH = 2  # 16-bit PCM = 2 bytes per sample
# MAX_CHUNK_SIZE = 65536  # 64KB per message - well below WebSocket limits


# def save_audio(file_name, audio_data):
#     """Save audio data to WAV file for debugging"""
#     with wave.open(file_name, "wb") as wf:
#         wf.setnchannels(1)  # Mono
#         wf.setsampwidth(SAMPLE_WIDTH)
#         wf.setframerate(RATE)
#         wf.writeframes(audio_data)


# def text_to_speech(text, cache_dir="tts_cache", max_length=500, speed_factor=1.7):
#     """
#     Convert text to speech using Google TTS and return as WAV bytes
#     Uses caching to avoid regenerating the same messages

#     Args:
#         text: Text to convert to speech
#         cache_dir: Directory to cache audio files
#         max_length: Maximum text length to process at once (to avoid large files)
#         speed_factor: Speed up the audio by this factor (higher = faster)
#     """
#     # Limit text length to avoid huge audio files
#     if len(text) > max_length:
#         print(
#             f"Warning: Text length ({len(text)}) exceeds maximum ({max_length}). Truncating..."
#         )
#         # Truncate at a sentence boundary if possible
#         truncation_point = text[:max_length].rfind(".")
#         if truncation_point == -1:
#             # If no sentence boundary, truncate at a space
#             truncation_point = text[:max_length].rfind(" ")

#         if (
#             truncation_point > max_length // 2
#         ):  # Only use truncation if we can keep at least half
#             text = text[: truncation_point + 1]
#         else:
#             text = text[:max_length]

#     # Create a hash of the text for caching
#     import hashlib

#     text_hash = hashlib.md5((text + f"_speed{speed_factor}").encode()).hexdigest()
#     cache_file = Path(cache_dir) / f"{text_hash}.wav"

#     # Check if we already have this audio cached
#     if cache_file.exists():
#         print(f"Using cached audio for: {text[:30]}...")
#         with open(cache_file, "rb") as f:
#             return f.read()

#     # Generate new TTS audio
#     print(f"Generating TTS for: {text[:30]}...")
#     tts = gtts.gTTS(text=text, lang="en", slow=False)

#     # Save as MP3 first (gtts only outputs MP3)
#     mp3_io = io.BytesIO()
#     tts.write_to_fp(mp3_io)
#     mp3_io.seek(0)

#     # Convert to WAV with parameters matching our audio system
#     # Apply speed factor to make speech faster
#     mp3_audio = AudioSegment.from_mp3(mp3_io)
#     wav_audio = (
#         mp3_audio.set_frame_rate(RATE).set_channels(1).set_sample_width(SAMPLE_WIDTH)
#     )

#     # Apply speedup
#     if speed_factor > 1.0:
#         wav_audio = wav_audio.speedup(playback_speed=speed_factor)

#     # Save WAV to cache
#     wav_io = io.BytesIO()
#     wav_audio.export(wav_io, format="wav")
#     wav_data = wav_io.getvalue()

#     # Save to cache file
#     with open(cache_file, "wb") as f:
#         f.write(wav_data)

#     return wav_data


# async def stream_audio_to_client(websocket, audio_data, message_id):
#     """Stream audio data to client in small chunks to avoid WebSocket size limits"""
#     chunk_size = MAX_CHUNK_SIZE  # Much smaller than the WebSocket limit
#     total_size = len(audio_data)
#     num_chunks = (total_size + chunk_size - 1) // chunk_size  # Ceiling division

#     print(f"Streaming {total_size} bytes of audio in {num_chunks} chunks")

#     # Signal the start of audio streaming
#     await websocket.send_text(
#         json.dumps(
#             {"type": "audio_start", "message_id": message_id, "total_size": total_size}
#         )
#     )

#     # Send the audio in chunks
#     for i in range(0, total_size, chunk_size):
#         end = min(i + chunk_size, total_size)
#         chunk = audio_data[i:end]

#         # Encode chunk as base64 to send as JSON
#         chunk_b64 = base64.b64encode(chunk).decode("utf-8")

#         await websocket.send_text(
#             json.dumps(
#                 {
#                     "type": "audio_chunk",
#                     "message_id": message_id,
#                     "data": chunk_b64,
#                     "is_last": end == total_size,
#                 }
#             )
#         )

#         # Short delay to avoid flooding
#         await asyncio.sleep(0.01)

#     print(f"Finished streaming audio for message {message_id}")


# @app.websocket("/ws")
# async def websocket_endpoint(websocket: WebSocket):
#     await websocket.accept()
#     print("Client connected")

#     # Store client-specific state
#     client_state = {
#         "session_id": f"session_{int(time.time())}",
#         "first_interaction": True,
#         "is_recording": False,
#         "received_audio_buffer": bytearray(),
#         "recording_start_time": None,
#     }

#     # Initialize conversation history
#     if not hasattr(health_agent, "history"):
#         health_agent.history = []

#     # Send welcome message as soon as client connects - using prompt from prompts.py
#     from src.prompts import WELCOME_PROMPT, READY_TO_START_PROMPT

#     # Get the initial message from HealthAgent
#     if health_agent.talk_mode == "START":
#         welcome_text = WELCOME_PROMPT.strip()
#         # Add the "ready to start" question
#         welcome_text += "\n\n" + READY_TO_START_PROMPT.strip()
#     else:
#         # If agent was already in a conversation, get the next question
#         welcome_text = health_agent.main_processor("")

#     # Calculate progress for interview state
#     progress = 0
#     if health_agent.predefined_questions:
#         progress = min(
#             100, (health_agent.idx / len(health_agent.predefined_questions) * 100)
#         )

#     # Send welcome text first
#     await websocket.send_text(
#         json.dumps(
#             {
#                 "type": "text_message",
#                 "text": welcome_text,
#                 "session_id": client_state["session_id"],
#                 "interview_state": {
#                     "section": health_agent.current_section,
#                     "progress": progress,
#                     "missing_fields": health_agent.missing_fields,
#                 },
#             }
#         )
#     )

#     # Generate welcome audio
#     welcome_audio = text_to_speech(welcome_text)

#     # Then stream welcome audio
#     await stream_audio_to_client(websocket, welcome_audio, "welcome_message")

#     # Main WebSocket loop
#     try:
#         while True:
#             # Receive message from client
#             message = await websocket.receive()

#             # Handle text messages (JSON control messages)
#             if "text" in message:
#                 try:
#                     data = json.loads(message["text"])
#                     msg_type = data.get("type", "")

#                     if msg_type == "audio_start":
#                         # Client is starting to send audio
#                         client_state["is_recording"] = True
#                         client_state["received_audio_buffer"] = bytearray()
#                         client_state["recording_start_time"] = data.get(
#                             "timestamp", time.time()
#                         )
#                         print("Client started sending audio")

#                     elif msg_type == "audio_end":
#                         # Client has finished sending audio
#                         client_state["is_recording"] = False

#                         # Process the accumulated audio
#                         audio_bytes = client_state["received_audio_buffer"]
#                         audio_duration = data.get("duration", 0)
#                         print(
#                             f"Received complete audio: {len(audio_bytes)} bytes, duration: {audio_duration:.2f}s"
#                         )

#                         # Skip processing if not enough audio data received
#                         if (
#                             len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5
#                         ):  # At least 0.5 seconds
#                             print(
#                                 f"Audio too short ({len(audio_bytes)} bytes), skipping"
#                             )
#                             await websocket.send_text(
#                                 json.dumps(
#                                     {
#                                         "type": "error",
#                                         "text": "Audio too short to process",
#                                     }
#                                 )
#                             )
#                             continue

#                         # Save received audio for debugging
#                         timestamp = int(time.time())
#                         file_path = f"audio_files/server_received_{timestamp}.wav"
#                         save_audio(file_path, audio_bytes)
#                         print(f"Saved audio to {file_path}")

#                         # Process audio with Whisper
#                         try:
#                             np_audio = (
#                                 np.frombuffer(audio_bytes, dtype=np.int16).astype(
#                                     np.float32
#                                 )
#                                 / 32768.0
#                             )

#                             # Skip if audio appears to be empty or corrupted
#                             if np_audio.size == 0 or np.max(np.abs(np_audio)) < 0.01:
#                                 print(
#                                     "⚠️ Audio seems silent or corrupted, skipping transcription"
#                                 )
#                                 await websocket.send_text(
#                                     json.dumps(
#                                         {
#                                             "type": "error",
#                                             "text": "Audio too quiet or corrupted",
#                                         }
#                                     )
#                                 )
#                                 continue

#                             # Process with Whisper
#                             audio = whisper.pad_or_trim(np_audio)
#                             mel = whisper.log_mel_spectrogram(audio).to(model.device)

#                             # Detect language
#                             _, probs = model.detect_language(mel)
#                             detected_lang = max(probs, key=probs.get)
#                             print(
#                                 f"Detected language: {detected_lang} (confidence: {probs[detected_lang]:.2f})"
#                             )

#                             # Decode audio
#                             options = whisper.DecodingOptions(
#                                 fp16=torch.cuda.is_available()
#                                 and cuda_device == "cuda",
#                                 language="en",  # Use detected_lang for multi-language support
#                             )
#                             result = whisper.decode(model, mel, options)

#                             # Extract transcription
#                             transcription = result.text.strip()
#                             print(f"✅ Transcription: {transcription}")

#                             # Save transcript for reference
#                             transcript_path = f"transcripts/transcript_{timestamp}.txt"
#                             with open(transcript_path, "w") as f:
#                                 f.write(transcription)

#                             # Process the transcription with HealthAgent
#                             response_text = health_agent.main_processor(transcription)

#                             # Check if we should save the form state (e.g., after completing a section)
#                             if health_agent.talk_mode != "START":
#                                 health_agent.save_progress()

#                             # Log the current state of the interview
#                             current_section = health_agent.current_section
#                             progress = 0
#                             if health_agent.form_sections:
#                                 current_index = health_agent.form_sections.index(
#                                     current_section
#                                 )
#                                 total_sections = len(health_agent.form_sections)
#                                 progress = (current_index / total_sections) * 100

#                             print(
#                                 f"Current section: {current_section}, Progress: {progress:.1f}%"
#                             )

#                             # Log missing fields if any
#                             if health_agent.missing_fields:
#                                 print(f"Missing fields: {health_agent.missing_fields}")

#                             # Calculate progress for the interview state
#                             progress = 0
#                             if health_agent.predefined_questions:
#                                 progress = min(
#                                     100,
#                                     (
#                                         health_agent.idx
#                                         / len(health_agent.predefined_questions)
#                                         * 100
#                                     ),
#                                 )

#                             # Send response text first
#                             await websocket.send_text(
#                                 json.dumps(
#                                     {
#                                         "type": "text_message",
#                                         "text": response_text,
#                                         "session_id": client_state["session_id"],
#                                         "interview_state": {
#                                             "section": health_agent.current_section,
#                                             "progress": progress,
#                                             "missing_fields": health_agent.missing_fields,
#                                         },
#                                     }
#                                 )
#                             )

#                             # Convert response to audio with faster speed
#                             response_audio = text_to_speech(
#                                 response_text, speed_factor=1.3
#                             )

#                             # Then stream response audio
#                             message_id = f"response_{timestamp}"
#                             await stream_audio_to_client(
#                                 websocket, response_audio, message_id
#                             )

#                         except Exception as e:
#                             print(f"Error processing audio: {e}")
#                             await websocket.send_text(
#                                 json.dumps(
#                                     {"type": "error", "text": f"Error: {str(e)}"}
#                                 )
#                             )

#                 except json.JSONDecodeError:
#                     print(f"Error decoding JSON: {message['text'][:100]}...")
#                 except Exception as e:
#                     print(f"Error handling text message: {e}")

#             # Handle binary messages (audio data)
#             elif "bytes" in message:
#                 if client_state["is_recording"]:
#                     # Accumulate audio chunks
#                     audio_chunk = message["bytes"]
#                     client_state["received_audio_buffer"].extend(audio_chunk)

#                     # Log progress
#                     total_kb = len(client_state["received_audio_buffer"]) / 1024
#                     chunk_kb = len(audio_chunk) / 1024
#                     print(
#                         f"Received audio chunk: {chunk_kb:.1f} KB, total: {total_kb:.1f} KB"
#                     )
#                 else:
#                     print("Received unexpected binary data when not recording")

#     except Exception as e:
#         print(f"WebSocket error: {e}")
#     finally:
#         print("Client disconnected")


# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8000)


from fastapi import (
    FastAPI,
    WebSocket,
    HTTPException,
    Query,
    UploadFile,
    File,
    Form,
)
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import numpy as np
import whisper
import torch
import wave
import time
import os
import base64
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
import uuid
from dotenv import load_dotenv
load_dotenv()
# Legacy imports (kept for compatibility even if audio streaming disabled)
import gtts  # Google Text-to-Speech
from pydub import AudioSegment  # For audio conversion
from pymongo import MongoClient
from pymongo.collection import Collection
from bson import ObjectId

from upload.s3_client import (
    generate_object_key,
    is_s3_configured,
    upload_bytes_to_s3,
)
from docscanner.service import summarize_report_from_bytes

# Import HealthAgent and related functionalities
from src.llm.functionalities import HealthAgent

# Import MongoDB database connector
# MongoDB DISABLED - Commented out
# from db import MedicalInterviewDB

app = FastAPI()

# Add CORS middleware to allow frontend connections
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://customerai.stance.health",
        "https://customer-agent-mu.vercel.app",
        "http://localhost:3000",
        "http://localhost:8080",
        "http://localhost:8000",
        "http://localhost:8081",
    ],  # Production frontend and local development origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories for saving audio and transcripts
os.makedirs("audio_files", exist_ok=True)
os.makedirs("tts_cache", exist_ok=True)
os.makedirs("transcripts", exist_ok=True)

# Load Whisper model
print("Loading Whisper model...")
cuda_device = "cuda" if torch.cuda.is_available() else "cpu"
model = whisper.load_model("base", device=cuda_device)
print(f"Model loaded on {cuda_device}")

# Initialize HealthAgent
print("Initializing HealthAgent...")
health_agent = HealthAgent()
print("HealthAgent initialized")

# MongoDB configuration
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "stance-dashboard")
MONGO_USERS_COLLECTION = os.getenv("MONGO_USERS_COLLECTION", "users")
MONGO_CUSTOMER_INFO_COLLECTION = "customer-info"
mongo_client = None
users_collection: Optional[Collection] = None
customer_info_collection: Optional[Collection] = None

ALLOWED_ATTACHMENT_TYPES = {
    "mri": "MRI Scan",
    "report": "Clinical Report",
    "other": "Supporting Document",
}
MAX_ATTACHMENT_SIZE_MB = 15
UPLOAD_TRIGGER_PHRASE = "do you have any mri, x-ray, ct scan, or blood reports"


def normalize_user_id(user_id: Optional[str]):
    """
    Normalize user_id to a MongoDB ObjectId where possible.
    - If user_id is a valid 24-char hex string, return ObjectId(user_id).
    - Otherwise, return the original value (e.g., for legacy string IDs).
    """
    if not user_id:
        return None
    try:
        return ObjectId(user_id)
    except Exception:
        # Fall back to storing as-is (string)
        return user_id


def init_mongo():
    """Initialize MongoDB client for user directory lookups and customer info."""
    global mongo_client, users_collection, customer_info_collection

    if not MONGO_URI:
        print("MONGO_URI not set. User suggestions and customer info endpoints will be disabled.")
        return

    try:
        mongo_client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=5000,
            tlsAllowInvalidCertificates=True,
        )
        mongo_client.admin.command("ping")
        db = mongo_client[MONGO_DB_NAME]
        users_collection = db[MONGO_USERS_COLLECTION]
        customer_info_collection = db[MONGO_CUSTOMER_INFO_COLLECTION]
        print(
            f"Connected to MongoDB collections: {MONGO_DB_NAME}.{MONGO_USERS_COLLECTION}, {MONGO_DB_NAME}.{MONGO_CUSTOMER_INFO_COLLECTION}"
        )
    except Exception as e:
        mongo_client = None
        users_collection = None
        customer_info_collection = None
        print(f"Failed to connect to MongoDB. User suggestions and customer info disabled: {e}")


def _serialize_datetime(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return str(value)
    except Exception:
        return ""


def serialize_user(doc):
    """Transform MongoDB user document into API-friendly shape."""
    if not doc:
        return None

    first = (
        doc.get("firstName")
        or doc.get("first_name")
        or doc.get("profileData", {}).get("firstName")
        or doc.get("personalDetails", {}).get("firstName")
        or ""
    )
    last = (
        doc.get("lastName")
        or doc.get("last_name")
        or doc.get("profileData", {}).get("lastName")
        or doc.get("personalDetails", {}).get("lastName")
        or ""
    )
    full = " ".join([part for part in [first, last] if part]).strip()
    if not full:
        full = (
            doc.get("fullName")
            or doc.get("name")
            or doc.get("profileData", {}).get("fullName")
            or doc.get("profileData", {}).get("name")
            or doc.get("personalDetails", {}).get("fullName")
            or doc.get("personalDetails", {}).get("name")
            or doc.get("email")
            or ""
        )

    return {
        "id": str(doc.get("_id")),
        "firstName": first,
        "lastName": last,
        "fullName": full,
    }


def generate_form_title(form_data: dict) -> str:
    """
    Generate a title for the form based on primary complaint using LLM.
    
    Args:
        form_data: The form data dictionary
        
    Returns:
        A title string based on the primary complaint
    """
    try:
        # Extract primary complaint from form data
        primary_complaint = form_data.get("Present Complaint", {}).get("Primary Complaint", "")
        
        if not primary_complaint:
            # Fallback to a generic title
            return "Medical Interview Form"
        
        # Use LLM to generate a concise title based on primary complaint
        prompt = f"""Based on the following primary complaint, generate a concise, descriptive title (maximum 50 characters) for this medical interview form.

Primary Complaint: {primary_complaint}

Generate only the title, nothing else. The title should be clear and based on the primary complaint."""
        
        if health_agent and health_agent.llm:
            response = health_agent.llm_complete(prompt)
            title = response.strip()
            # Clean up the title - remove quotes if present
            title = title.strip('"').strip("'").strip()
            # Limit length
            if len(title) > 50:
                title = title[:47] + "..."
            return title if title else f"Medical Interview: {primary_complaint[:30]}"
        else:
            # Fallback title
            return f"Medical Interview: {primary_complaint[:30]}"
    except Exception as e:
        print(f"Error generating form title: {e}")
        # Fallback to primary complaint or generic title
        primary_complaint = form_data.get("Present Complaint", {}).get("Primary Complaint", "")
        if primary_complaint:
            return f"Medical Interview: {primary_complaint[:30]}"
        return "Medical Interview Form"


# Fixed form ID - same for all users. Uniqueness comes from (formId + userId) combination
DEFAULT_FORM_ID = "FRM-01"

def save_customer_info(
    user_id: str,
    form_data: dict,
    current_section: str,
    form_id: str = None,
    attachments: Optional[List[dict]] = None,
):
    """
    Save or update customer interview information in MongoDB.
    Forms are unique by the combination of (formId + userId).
    The formId is fixed (same for all users), uniqueness comes from userId.
    
    Args:
        user_id: The user ID from the selected user (required for uniqueness)
        form_data: The form data dictionary
        current_section: Current section of the interview
        form_id: Optional form ID. If None, uses DEFAULT_FORM_ID.
    
    Returns:
        The form_id of the saved form (always DEFAULT_FORM_ID)
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        # Try to reconnect once
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available. Skipping MongoDB save.")
        return None
    
    if not user_id:
        print("Error: user_id is required for saving form data")
        return None
    
    try:
        from datetime import datetime, timezone
        import copy
        
        # Normalize user_id to ObjectId where possible
        normalized_user_id = normalize_user_id(user_id)

        # Use fixed form ID (same for all users)
        if not form_id:
            form_id = DEFAULT_FORM_ID
            print(f"[save_customer_info] Using default form_id: {form_id} for user: {user_id}")
        else:
            # If form_id is provided, use it (but typically should be DEFAULT_FORM_ID)
            print(f"[save_customer_info] Using provided form_id: {form_id} for user: {user_id}")
        
        # CRITICAL: Deep copy form_data to prevent shared references
        # This ensures each form has its own independent copy of the data
        form_data_copy = copy.deepcopy(form_data)
        
        # Generate title based on primary complaint (use "New Form" if form is empty)
        primary_complaint = form_data_copy.get("Present Complaint", {}).get("Primary Complaint", "")
        if not primary_complaint or not primary_complaint.strip():
            form_title = "New Form"
        else:
            form_title = generate_form_title(form_data_copy)
        
        # Prepare the document matching the medical_interview_history.json structure
        customer_doc = {
            "userId": normalized_user_id,
            "formId": form_id,
            "title": form_title,
            "timestamp": datetime.now().isoformat(),
            "form_data": form_data_copy,  # Use the deep copy
            "current_section": current_section,
            "updatedAt": datetime.now(),
        }
        
        # Check if document exists for this (formId + userId) combination
        # Uniqueness is based on BOTH formId AND userId (normalized)
        existing = customer_info_collection.find_one({
            "formId": form_id,
            "userId": normalized_user_id
        })
        
        if existing:
            attachments_to_store = (
                attachments if attachments is not None else existing.get("attachments", [])
            )
            # Update existing document
            customer_doc["createdAt"] = existing.get("createdAt", datetime.now())
            customer_doc["attachments"] = attachments_to_store
            customer_info_collection.update_one(
                {"formId": form_id, "userId": normalized_user_id},
                {"$set": customer_doc}
            )
            print(f"Updated customer info for user {user_id}, form {form_id} in MongoDB")
        else:
            # Create new document
            customer_doc["createdAt"] = datetime.now()
            customer_doc["attachments"] = attachments if attachments is not None else []
            customer_info_collection.insert_one(customer_doc)
            print(f"Created customer info for user {user_id}, form {form_id} in MongoDB")
        
        return form_id
            
    except Exception as e:
        print(f"Error saving customer info to MongoDB: {e}")
        return None


def fetch_user_forms(user_id: str) -> list:
    """
    Retrieve all forms for a specific user from MongoDB.
    
    Args:
        user_id: The user ID to retrieve forms for
        
    Returns:
        List of form documents with formId, title, timestamp, etc.
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return []
    
    try:
        # Since formId is fixed, find by (formId + userId) combination
        # Normalize user_id to ObjectId where possible
        normalized_user_id = normalize_user_id(user_id)
        # Each user should have at most one form
        forms = list(
            customer_info_collection.find(
                {
                    "userId": normalized_user_id,
                    "formId": DEFAULT_FORM_ID
                },
                {"_id": 0}
            ).sort("createdAt", -1)
        )
        
        formatted_forms = []
        for form in forms:
            formatted_forms.append({
                "formId": form.get("formId", ""),
                "title": form.get("title", "Untitled Form"),
                "timestamp": _serialize_datetime(form.get("timestamp")),
                "createdAt": _serialize_datetime(form.get("createdAt")),
                "updatedAt": _serialize_datetime(form.get("updatedAt")),
                "current_section": form.get("current_section", ""),
                "progress": calculate_form_progress(form.get("form_data", {}))
            })
        
        return formatted_forms
    except Exception as e:
        print(f"Error retrieving user forms: {e}")
        return []


def fetch_latest_form_for_user(user_id: str) -> dict:
    """
    Retrieve the form for a specific user.
    Since formId is fixed, each user has exactly one form (identified by formId + userId).
    """
    global customer_info_collection

    if customer_info_collection is None:
        init_mongo()

    if customer_info_collection is None:
        print("Warning: customer-info collection not available when fetching latest form.")
        return None

    try:
        # Find form by (formId + userId) combination
        normalized_user_id = normalize_user_id(user_id)
        form = customer_info_collection.find_one(
            {
                "userId": normalized_user_id,
                "formId": DEFAULT_FORM_ID
            },
            {"_id": 0},
            sort=[("createdAt", -1)],
        )
        if form:
            form["timestamp"] = _serialize_datetime(form.get("timestamp"))
            form["createdAt"] = _serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = _serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"Error retrieving form for user {user_id}: {e}")
        return None


def fetch_form_by_id(form_id: str, user_id: str = None) -> dict:
    """
    Retrieve a specific form by form_id and user_id.
    Since forms are unique by (formId + userId) combination, user_id is required.
    
    Args:
        form_id: The form ID to retrieve
        user_id: The user ID (required for uniqueness)
        
    Returns:
        Form document or None if not found
    """
    global customer_info_collection
    
    if customer_info_collection is None:
        init_mongo()
    
    if customer_info_collection is None:
        print("Warning: customer-info collection not available.")
        return None
    
    if not user_id:
        print("Warning: user_id is required to fetch form by form_id")
        return None
    
    try:
        # Find form by (formId + userId) combination
        normalized_user_id = normalize_user_id(user_id)
        form = customer_info_collection.find_one(
            {
                "formId": form_id,
                "userId": normalized_user_id
            },
            {"_id": 0}
        )
        if form:
            form["timestamp"] = _serialize_datetime(form.get("timestamp"))
            form["createdAt"] = _serialize_datetime(form.get("createdAt"))
            form["updatedAt"] = _serialize_datetime(form.get("updatedAt"))
        return form
    except Exception as e:
        print(f"Error retrieving form: {e}")
        return None


def fetch_form_attachments(form_id: str, user_id: str = None) -> List[dict]:
    """
    Retrieve attachments for a form.
    Since forms are unique by (formId + userId), user_id is required.
    """
    global customer_info_collection

    if customer_info_collection is None:
        init_mongo()

    if customer_info_collection is None:
        return []

    if not user_id:
        print("Warning: user_id is required to fetch form attachments")
        return []

    try:
        normalized_user_id = normalize_user_id(user_id)
        doc = customer_info_collection.find_one(
            {
                "formId": form_id,
                "userId": normalized_user_id
            },
            {"_id": 0, "attachments": 1},
        )
        return doc.get("attachments", []) if doc else []
    except Exception as e:
        print(f"Error retrieving attachments for form {form_id}: {e}")
        return []


def create_placeholder_form(user_id: str, client_state: dict) -> Optional[str]:
    """Create an empty form record so uploads have a form_id to reference."""
    import copy
    # CRITICAL: Use get_medical_form_template() to get a fresh template
    # This ensures we always start with a clean template that hasn't been mutated
    from src.prompts import get_medical_form_template
    
    # Get a fresh template (always clean, even if MEDICAL_FORM_TEMPLATE was mutated)
    form_data_copy = get_medical_form_template()
    
    # VERIFY the copied form is actually empty before using it
    has_data_in_copy = False
    for section, fields in form_data_copy.items():
        if isinstance(fields, dict):
            for field, value in fields.items():
                if value and str(value).strip():
                    has_data_in_copy = True
                    print(f"[create_placeholder_form] ERROR: Deep copied template has non-empty data! {section}.{field} = {value}")
    
    if has_data_in_copy:
        print(f"[create_placeholder_form] CRITICAL ERROR: Deep copied template still has data! Creating fresh empty form manually.")
        # Manually create an empty form structure as last resort
        form_data_copy = {
            "Present Complaint": {
                "Primary Complaint": "",
                "Duration of the Issue": "",
                "Onset (Gradual or Sudden)": "",
                "Mechanism of Injury (If Any)": "",
            },
            "Previous Consultations": {
                "Previous Diagnosis or Advice and Prescribed Treatment Taken": "",
                "Current Status of Issue (Improved, Same, Worse)": "",
            },
            "Pain Assessment": {
                "Primary Location of Pain": "",
                "Severity (1-10)": "",
                "Aggravating Factors": "",
                "Relieving Factors": "",
            },
            "Medical History": {
                "Systemic Illness and Surgical History": "",
            },
            "Lifestyle Factors": {
                "Current Lifestyle": "",
            },
            "Treatment Goals": {
                "Short-Term Goals (within 3 months)": "",
                "Long-Term Goals (after 3 months)": "",
                "Specific Expectations from Treatment": "",
            },
            "Diagnostic Reports": {
                "Reports": "",
            },
            "Referral": {
                "Source": "",
            },
        }
    
    print(f"[create_placeholder_form] Creating new form with empty template. Form keys: {list(form_data_copy.keys())}")
    
    # Double-check: Verify form_data_copy is empty before saving
    for section, fields in form_data_copy.items():
        if isinstance(fields, dict):
            for field, value in fields.items():
                if value and str(value).strip():
                    print(f"[create_placeholder_form] CRITICAL: form_data_copy has data before save! {section}.{field} = {value}")
    
    # Get the first section as current_section (should be from template, not health_agent)
    current_section = list(form_data_copy.keys())[0] if form_data_copy else "Present Complaint"
    
    form_id = save_customer_info(
        user_id=user_id,
        form_data=form_data_copy,
        current_section=current_section,
        form_id=None,  # Explicitly None to force new form creation
        attachments=[],
    )
    if form_id:
        client_state["form_id"] = form_id
        print(f"[create_placeholder_form] Created new form_id: {form_id} for user: {user_id}")
        
        # Immediately verify what was actually saved
        saved_form = fetch_form_by_id(form_id, user_id)
        if saved_form:
            saved_data = saved_form.get("form_data", {})
            for section, fields in saved_data.items():
                if isinstance(fields, dict):
                    for field, value in fields.items():
                        if value and str(value).strip():
                            print(f"[create_placeholder_form] CRITICAL ERROR: Form {form_id} was saved with data! {section}.{field} = {value}")
    return form_id


def build_interview_state(client_state: dict, progress_override: Optional[float] = None, use_db_form: bool = False):
    """
    Build interview state for the client.
    
    CRITICAL: Always prefer database form data over health_agent.form to ensure
    each form is unique by (formId + userId) combination.
    
    Args:
        client_state: Current client state dictionary (must contain user_id and form_id)
        progress_override: Optional progress percentage override
        use_db_form: If True, fetch form data from database for accurate progress calculation
    """
    form_id = client_state.get("form_id")
    user_id = client_state.get("user_id")
    attachments = fetch_form_attachments(form_id, user_id) if form_id and user_id else []
    
    # CRITICAL: Always prefer database form for progress calculation to ensure independence
    # Calculate progress - prefer database form if available (more accurate after uploads)
    form_data_for_progress = None
    if form_id and user_id:
        # Always fetch from database when form_id exists to ensure independence
        form = fetch_form_by_id(form_id, user_id)
        if form and form.get("form_data"):
            form_data_for_progress = form["form_data"]
        else:
            # Fallback to health_agent.form only if form not found in DB
            form_data_for_progress = health_agent.form
    else:
        # No form_id yet, use health_agent.form (will be empty for new forms)
        form_data_for_progress = health_agent.form
    
    # Calculate overall progress
    if progress_override is not None:
        progress = progress_override
    else:
        progress = calculate_form_progress(form_data_for_progress)
    
    # Calculate section completion status with ordering
    section_progress = calculate_section_completion_status(form_data_for_progress)
    
    return {
        "section": health_agent.current_section,
        "progress": progress,
        "missing_fields": health_agent.missing_fields,
        "attachments": attachments,
        "formId": form_id,
        "sectionProgress": section_progress,  # Added: section completion status with ordering
    }


def user_has_reports(user_response: str) -> bool:
    """
    Check if the user's response indicates they have reports.
    
    Args:
        user_response: The user's text response
        
    Returns:
        True if user indicates they have reports, False otherwise
    """
    if not user_response:
        return False
    
    normalized = user_response.lower().strip()
    
    # Special handling: phrases like "i do / i do not" combined with "report"
    if "report" in normalized or "reports" in normalized:
        if ("i do not" in normalized) or ("i don't" in normalized) or ("i dont" in normalized):
            print("[user_has_reports] Detected 'i do not' with reports → treating as NO reports")
            return False
        if "i do" in normalized and not ("i do not" in normalized or "i don't" in normalized or "i dont" in normalized):
            print("[user_has_reports] Detected 'i do' with reports → treating as HAS reports")
            return True
    
    # Positive indicators that user has reports
    positive_indicators = [
        "yes", "yeah", "yep", "i have", "i do have", "i've got", "i got",
        "i have reports", "i have mri", "i have x-ray", "i have x ray",
        "i have ct scan", "i have blood reports", "i have scans",
        "yes i have", "yes i do", "yes i do have", "i do have reports",
        "i have some", "i have them", "yes i have them", "i got reports",
        "i have the reports", "i have my reports", "i brought", "i brought reports"
    ]
    
    # Negative indicators that user doesn't have reports
    negative_indicators = [
        "no", "nope",
        "don't have", "do not have", "don't have any",
        "dont have", "dont have any",
        "no i don't", "no i do not",
        "no i dont", "no i dont have",
        "i don't have", "i do not have",
        "i dont have", "i dont have any",
        "no reports", "no i don't have", "no i dont have reports",
        "none", "nothing", "no i haven't",
        "i haven't", "i have not",
        "no scans", "no mri", "no x-ray"
    ]
    
    # Check for negative first (more definitive)
    for neg in negative_indicators:
        if neg in normalized:
            print(f"[user_has_reports] User indicated NO reports: '{neg}' found in response")
            return False
    
    # Check for positive indicators
    for pos in positive_indicators:
        if pos in normalized:
            print(f"[user_has_reports] User indicated they HAVE reports: '{pos}' found in response")
            return True
    
    # If no clear indicator, check if they mention having specific report types
    report_types = ["mri", "x-ray", "x ray", "ct scan", "blood report", "scan", "reports"]
    has_report_types = any(rt in normalized for rt in report_types)
    
    if has_report_types:
        # If they mention report types without negative words, assume they have them
        print(f"[user_has_reports] User mentioned report types, assuming they have reports")
        return True
    
    return False


def message_requires_attachment(
    current_section: Optional[str], 
    message_text: Optional[str] = None, 
    form_id: Optional[str] = None,
    user_response: Optional[str] = None,
    user_id: Optional[str] = None
) -> bool:
    """
    Show upload card ONLY if:
    1. We're in History & Diagnostics section AND
    2. The user's previous response indicated they HAVE reports (not when asking the question)
    
    Args:
        current_section: Current section name
        message_text: The assistant's message text (for logging)
        form_id: Form ID to check existing attachments
        user_response: The user's response text (to check if they said they have reports)
        user_id: User ID (required to fetch form by formId + userId combination)
    """
    try:
        # If we already have reports attachments for this form, don't ask again
        if form_id and user_id:
            form = fetch_form_by_id(form_id, user_id)
            if form:
                attachments = form.get("attachments", [])
                has_attachments = bool(attachments)
                if has_attachments:
                    print(f"[message_requires_attachment] Attachments already present, not showing upload")
                    return False
    except Exception as e:
        print(f"[message_requires_attachment] Warning: failed to check form attachments/reports: {e}")

    # If the agent is explicitly asking the user to upload reports, always request attachment
    # This is the follow-up prompt like:
    # "Great! Since you mentioned you have reports, would you like to upload them? ..."
    if message_text:
        lower_msg = message_text.lower()
        if "would you like to upload" in lower_msg or ("upload them" in lower_msg and "reports" in lower_msg):
            print("[message_requires_attachment] Agent is explicitly asking to upload reports, showing upload card")
            return True

    # CRITICAL: Only show upload if:
    # 1. We're in History & Diagnostics section
    # 2. The agent explicitly asks to upload reports
    #    (user intent about reports is now handled by the LLM inside HealthAgent,
    #     which generates the precise upload prompt text when appropriate).
    if current_section and "History & Diagnostics" in current_section:
        # If this is the initial diagnostic question, do NOT show upload yet
        if message_text:
            question_phrases = [
                "do you have any mri",
                "x-ray",
                "ct scan",
                "blood reports",
                "do you have any reports",
                "any reports related",
            ]
            if any(phrase in message_text.lower() for phrase in question_phrases):
                print("[message_requires_attachment] Agent is asking about reports, not showing upload card yet")
                return False
        else:
            # No user response yet (this is the question being asked), don't show upload
            print(f"[message_requires_attachment] Asking about reports, not showing upload yet (waiting for user response)")
            return False
    
    return False


async def send_text_message(
    websocket: WebSocket,
    client_state: dict,
    text: str,
    interview_state: Optional[dict] = None,
    user_response: Optional[str] = None,
):
    """
    Send a text message to the client.
    
    Args:
        websocket: WebSocket connection
        client_state: Current client state
        text: Message text to send
        interview_state: Optional interview state
        user_response: Optional user's previous response (to check if they have reports)
    """
    current_section = interview_state.get("section") if interview_state else health_agent.current_section
    requires_attachment = message_requires_attachment(
        current_section, 
        text, 
        client_state.get("form_id"),
        user_response=user_response,
        user_id=client_state.get("user_id")
    )
    
    payload = {
        "type": "text_message",
        "text": text,
        "session_id": client_state["session_id"],
        "interview_state": interview_state
        if interview_state is not None
        else build_interview_state(client_state),
        "request_attachment": requires_attachment,
    }
    await websocket.send_text(json.dumps(payload))


def reset_health_agent_for_new_interview(client_state: dict, new_user_id: str):
    """
    Fully reset interview state when a (possibly new) user starts an interview.

    This ensures that:
    - No previous user's form data or progress leaks into the new session
    - Progress always starts from 0% for a fresh interview
    - A new placeholder form is created in MongoDB unless an explicit load_form is called
    """
    global health_agent

    # If switching users, log it for debugging
    previous_user_id = client_state.get("user_id")
    if previous_user_id and previous_user_id != new_user_id:
        print(f"[reset_health_agent] User switched from {previous_user_id} to {new_user_id}. Resetting health_agent.")
    elif previous_user_id == new_user_id:
        print(f"[reset_health_agent] Same user {new_user_id} starting new interview. Resetting health_agent.")
    else:
        print(f"[reset_health_agent] New user {new_user_id} starting interview. Resetting health_agent.")

    # Always reset HealthAgent form + conversation state for any (re)start
    # This is critical to prevent data leakage between users or sessions
    health_agent.init_form()  # Clears JSON file + resets form, idx, current_section, etc.
    health_agent.talk_mode = "START"
    health_agent.history = []
    health_agent.history.append({"role": "agent", "message": health_agent.welcome_prompt})

    # Reset per-connection form linkage so we don't accidentally reuse an old form_id
    client_state["user_id"] = new_user_id
    client_state["form_id"] = None
    
    print(f"[reset_health_agent] HealthAgent reset complete. Form is now empty: {len(health_agent.form)} sections")


# Initialize database
# MongoDB DISABLED - Commented out
# print("Connecting to MongoDB...")
# db = MedicalInterviewDB()
# print("MongoDB connected")
init_mongo()


@app.get("/health")
async def health_check():
    """Health check endpoint for Docker and monitoring."""
    return {"status": "healthy", "service": "healthflex-customer-agent"}


# Audio parameters
RATE = 16000  # 16kHz
SAMPLE_WIDTH = 2  # 16-bit PCM = 2 bytes per sample
MAX_CHUNK_SIZE = 65536  # 64KB per message - well below WebSocket limits


def save_audio(file_name, audio_data):
    """Save audio data to WAV file for debugging"""
    with wave.open(file_name, "wb") as wf:
        wf.setnchannels(1)  # Mono
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(RATE)
        wf.writeframes(audio_data)


def text_to_speech(text, cache_dir="tts_cache", max_length=500, speed_factor=1.7):
    """
    Convert text to speech using Google TTS and return as WAV bytes
    Uses caching to avoid regenerating the same messages

    Args:
        text: Text to convert to speech
        cache_dir: Directory to cache audio files
        max_length: Maximum text length to process at once (to avoid large files)
        speed_factor: Speed up the audio by this factor (higher = faster)
    """
    # Limit text length to avoid huge audio files
    if len(text) > max_length:
        print(
            f"Warning: Text length ({len(text)}) exceeds maximum ({max_length}). Truncating..."
        )
        # Truncate at a sentence boundary if possible
        truncation_point = text[:max_length].rfind(".")
        if truncation_point == -1:
            # If no sentence boundary, truncate at a space
            truncation_point = text[:max_length].rfind(" ")

        if (
            truncation_point > max_length // 2
        ):  # Only use truncation if we can keep at least half
            text = text[: truncation_point + 1]
        else:
            text = text[:max_length]

    # Create a hash of the text for caching
    import hashlib

    text_hash = hashlib.md5((text + f"_speed{speed_factor}").encode()).hexdigest()
    cache_file = Path(cache_dir) / f"{text_hash}.wav"

    # Check if we already have this audio cached
    if cache_file.exists():
        print(f"Using cached audio for: {text[:30]}...")
        with open(cache_file, "rb") as f:
            return f.read()

    # Generate new TTS audio
    print(f"Generating TTS for: {text[:30]}...")
    tts = gtts.gTTS(text=text, lang="en", slow=False)

    # Save as MP3 first (gtts only outputs MP3)
    mp3_io = io.BytesIO()
    tts.write_to_fp(mp3_io)
    mp3_io.seek(0)

    # Convert to WAV with parameters matching our audio system
    # Apply speed factor to make speech faster
    mp3_audio = AudioSegment.from_mp3(mp3_io)
    wav_audio = (
        mp3_audio.set_frame_rate(RATE).set_channels(1).set_sample_width(SAMPLE_WIDTH)
    )

    # Apply speedup
    if speed_factor > 1.0:
        wav_audio = wav_audio.speedup(playback_speed=speed_factor)

    # Save WAV to cache
    wav_io = io.BytesIO()
    wav_audio.export(wav_io, format="wav")
    wav_data = wav_io.getvalue()

    # Save to cache file
    with open(cache_file, "wb") as f:
        f.write(wav_data)

    return wav_data


def calculate_form_progress(form_data):
    """
    Calculate progress based on individual field completion (not just sections).
    Progress is calculated as: (filled_fields / total_fields) * 100
    
    Args:
        form_data: Dictionary containing form sections and their fields
        
    Returns:
        Progress percentage (0-100)
    """
    if not form_data:
        return 0
    
    # Define expected sections (must match section_order in calculate_section_completion_status)
    expected_sections = [
        "Present Complaint",
        "Previous Consultations",
        "Pain Assessment",
        "History & Diagnostics",
        "Treatment Goals",
        "Referral"
    ]
    
    total_fields = 0
    filled_fields = 0
    
    for section_name in expected_sections:
        if section_name not in form_data:
            continue
            
        section_data = form_data[section_name]
        if not isinstance(section_data, dict):
            continue
        
        # Special handling for "Previous Consultations" section
        if section_name == "Previous Consultations":
            previous_consultations_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
            status_field = "Current Status of Issue (Improved, Same, Worse)"
            
            previous_value = section_data.get(previous_consultations_field, "")
            if previous_value and str(previous_value).strip():
                previous_value_lower = str(previous_value).strip().lower()
                no_consultation_indicators = [
                    "no previous", "didn't visit", "did not visit", "haven't consulted",
                    "have not consulted", "no consultations", "no doctor", "no hospital",
                    "never consulted", "not consulted", "none", "nothing"
                ]
                has_no_consultations = any(indicator in previous_value_lower for indicator in no_consultation_indicators)
                
                # If no previous consultations, only count 1 field (the previous consultations field itself)
                if has_no_consultations:
                    total_fields += 1
                    filled_fields += 1  # The "no consultations" answer counts as filled
                else:
                    # Normal case - both fields need to be filled
                    total_fields += 2
                    if previous_value and str(previous_value).strip():
                        filled_fields += 1
                    if section_data.get(status_field) and str(section_data.get(status_field, "")).strip():
                        filled_fields += 1
            else:
                # No value yet, both fields are required
                total_fields += 2
                if previous_value and str(previous_value).strip():
                    filled_fields += 1
                if section_data.get(status_field) and str(section_data.get(status_field, "")).strip():
                    filled_fields += 1
        else:
            # For all other sections, count all fields
            for field_name, field_value in section_data.items():
                total_fields += 1
                if field_value and str(field_value).strip():
                    field_lower = str(field_value).strip().lower()
                    # For History & Diagnostics and Referral, "none", "no", "nothing" are valid answers,
                    # but generic values like "yes" for Referral.Source are NOT (they don't tell us the source).
                    if section_name == "History & Diagnostics":
                        filled_fields += 1
                    elif section_name == "Referral" and field_name == "Source":
                        if field_lower not in ["yes", "no", "none", "n/a", "na", ""]:
                            filled_fields += 1
                    else:
                        # For other sections, don't count "none", "no", "nothing", "n/a" as filled
                        if field_lower not in ["none", "no", "nothing", "n/a", "na", ""]:
                            filled_fields += 1
    
    if total_fields == 0:
        return 0
    
    progress = (filled_fields / total_fields) * 100
    return min(100, max(0, round(progress, 1)))


def calculate_section_completion_status(form_data):
    """
    Calculate completion status for each section with ordering.
    Completed sections come first, incomplete sections at the bottom.
    
    Args:
        form_data: Dictionary containing form sections and their fields
        
    Returns:
        Dictionary with:
        - totalSteps: Total number of sections
        - currentStep: Number of completed sections + 1 (current section being worked on)
        - progress: Overall progress percentage
        - steps: List of sections with completion status, ordered (completed first, incomplete at bottom)
    """
    if not form_data:
        return {
            "totalSteps": 0,
            "currentStep": 0,
            "progress": 0,
            "steps": []
        }
    
    # Define section order (as they appear in the form template)
    # NOTE: This must match the actual form structure in prompts.py
    section_order = [
        "Present Complaint",
        "Previous Consultations",
        "Pain Assessment",
        "History & Diagnostics",  # Combined section (not separate Medical History, Lifestyle Factors, Diagnostic Reports)
        "Treatment Goals",
        "Referral"
    ]
    
    completed_sections = []
    incomplete_sections = []
    
    for section_name in section_order:
        if section_name not in form_data:
            continue
            
        section_data = form_data[section_name]
        if not isinstance(section_data, dict):
            continue
        
        # Calculate completion for this section
        filled_fields = 0
        # Special handling for "Previous Consultations" section
        if section_name == "Previous Consultations":
            previous_consultations_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
            status_field = "Current Status of Issue (Improved, Same, Worse)"
            
            # Check if user indicated no previous consultations
            previous_value = section_data.get(previous_consultations_field, "")
            status_value = section_data.get(status_field, "")
            
            # CRITICAL: Check if data was incorrectly extracted
            # If status is filled but no consultations mentioned, it's likely incorrect extraction
            consultation_keywords = [
                "doctor", "physiotherapist", "hospital", "consulted", "visited",
                "diagnosis", "diagnosed", "prescribed", "treatment", "medicine",
                "injection", "exercise", "physio", "clinic"
            ]
            has_consultation_mention = False
            if previous_value and str(previous_value).strip():
                has_consultation_mention = any(
                    keyword in str(previous_value).strip().lower() 
                    for keyword in consultation_keywords
                )
            
            # If status is filled but no consultations mentioned, don't count it as complete
            if status_value and str(status_value).strip() and not previous_value and not has_consultation_mention:
                # This is likely incorrect extraction - status shouldn't be filled without consultations
                print(f"[calculate_section_completion_status] Previous Consultations: Status field filled but no consultations mentioned - treating as incomplete")
                total_fields = 2
                filled_fields = 0  # Don't count incorrectly extracted data
            elif previous_value and str(previous_value).strip():
                previous_value_lower = str(previous_value).strip().lower()
                no_consultation_indicators = [
                    "no previous", "didn't visit", "did not visit", "haven't consulted",
                    "have not consulted", "no consultations", "no doctor", "no hospital",
                    "never consulted", "not consulted", "none", "nothing"
                ]
                has_no_consultations = any(indicator in previous_value_lower for indicator in no_consultation_indicators)
                
                # If no previous consultations, only the previous consultations field needs to be filled
                if has_no_consultations:
                    total_fields = 1  # Only count the previous consultations field
                    filled_fields = 1
                else:
                    # Normal case - both fields need to be filled
                    total_fields = 2
                    if previous_value and str(previous_value).strip():
                        filled_fields += 1
                    if status_value and str(status_value).strip():
                        filled_fields += 1
            else:
                # No value yet, both fields are required
                total_fields = 2
                filled_fields = 0
        else:
            # For all other sections, check all fields normally
            total_fields = len(section_data)
            if total_fields == 0:
                # Empty section, consider incomplete
                incomplete_sections.append({
                    "name": section_name,
                    "isComplete": False,
                    "completionPercentage": 0,
                    "filledFields": 0,
                    "totalFields": 0
                })
                continue
            
            for field_name, field_value in section_data.items():
                # Check if field has meaningful data
                if field_value and str(field_value).strip():
                    field_lower = str(field_value).strip().lower()
                    # For History & Diagnostics and Referral, "None", "no", "nothing" are valid answers
                    # (they indicate the user answered the question)
                    if section_name == "History & Diagnostics" or section_name == "Referral":
                        filled_fields += 1
                    else:
                        # For other sections, don't count "none", "no", "nothing", "n/a" as filled
                        if field_lower not in ["none", "no", "nothing", "n/a", "na", ""]:
                            filled_fields += 1
        
        completion_percentage = (filled_fields / total_fields) * 100 if total_fields > 0 else 0
        # Mark a section as complete (ticked) only when ALL fields are filled
        is_complete = filled_fields == total_fields and total_fields > 0
        
        # Debug logging for History & Diagnostics and Referral
        if section_name == "History & Diagnostics":
            print(f"[calculate_section_completion_status] History & Diagnostics - filled_fields: {filled_fields}, total_fields: {total_fields}, is_complete: {is_complete}, section_data: {section_data}")
        elif section_name == "Referral":
            print(f"[calculate_section_completion_status] Referral - filled_fields: {filled_fields}, total_fields: {total_fields}, is_complete: {is_complete}, section_data: {section_data}")
        
        section_info = {
            "name": section_name,
            "isComplete": is_complete,
            "completionPercentage": round(completion_percentage, 1),
            "filledFields": filled_fields,
            "totalFields": total_fields,
        }
        
        if is_complete:
            completed_sections.append(section_info)
        else:
            incomplete_sections.append(section_info)
    
    # Combine: completed first, then incomplete (stack ordering)
    all_sections = completed_sections + incomplete_sections
    
    # Calculate overall progress based on fully completed sections (all fields filled)
    total_sections = len(all_sections)
    completed_count = len(completed_sections)
    progress = (completed_count / total_sections) * 100 if total_sections > 0 else 0
    
    # Current step is the first incomplete section (or total if all complete)
    current_step = completed_count + 1 if incomplete_sections else total_sections
    
    return {
        "totalSteps": total_sections,
        "currentStep": current_step,
        "completedSteps": completed_count,  # Explicitly include completed count for frontend
        "progress": round(progress, 1),
        "steps": all_sections
    }


async def stream_audio_to_client(websocket, audio_data, message_id):
    """Stream audio data to client in small chunks to avoid WebSocket size limits"""
    chunk_size = MAX_CHUNK_SIZE  # Much smaller than the WebSocket limit
    total_size = len(audio_data)
    num_chunks = (total_size + chunk_size - 1) // chunk_size  # Ceiling division

    print(f"Streaming {total_size} bytes of audio in {num_chunks} chunks")

    # Signal the start of audio streaming
    await websocket.send_text(
        json.dumps(
            {"type": "audio_start", "message_id": message_id, "total_size": total_size}
        )
    )

    # Send the audio in chunks
    for i in range(0, total_size, chunk_size):
        end = min(i + chunk_size, total_size)
        chunk = audio_data[i:end]

        # Encode chunk as base64 to send as JSON
        chunk_b64 = base64.b64encode(chunk).decode("utf-8")

        await websocket.send_text(
            json.dumps(
                {
                    "type": "audio_chunk",
                    "message_id": message_id,
                    "data": chunk_b64,
                    "is_last": end == total_size,
                }
            )
        )

        # Short delay to avoid flooding
        await asyncio.sleep(0.01)

    print(f"Finished streaming audio for message {message_id}")


# def modify_system_prompt():
#     """
#     Modify the system prompt to make the agent more strict about not making assumptions.
#     This modifies the HealthAgent's system prompt directly.
#     """
#     original_prompt = health_agent.system_prompt

#     # Add instructions to avoid making assumptions
#     assumption_instructions = """IMPORTANT: Stick to the question format as much as possible. They are presets."""

#     # Combine with original prompt
#     health_agent.system_prompt = original_prompt + assumption_instructions
#     print("Modified system prompt to reduce assumptions")


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    print(f"Client {client_id} connected")

    # Store client-specific state
    client_state = {
        "session_id": f"session_{int(time.time())}",
        # user_id will be provided by the frontend (real app user ID)
        # via the "start_interview" message. We intentionally avoid
        # generating placeholder IDs like "user_<timestamp>" so that
        # all MongoDB documents are tied to real users from your system.
        "user_id": None,
        "interview_id": None,
        "form_id": None,
        "first_interaction": True,
        "is_recording": False,
        "received_audio_buffer": bytearray(),
        "recording_start_time": None,
    }

    # Create a new interview record
    # MongoDB DISABLED - Using placeholder values instead
    # interview_id = db.create_interview(user_id, client_state["session_id"])
    interview_id = f"interview_{int(time.time())}"  # Placeholder interview ID
    client_state["interview_id"] = interview_id
    print(f"WebSocket session started with interview {interview_id} (user will be set on start_interview)")

    # Initialize conversation history
    if not hasattr(health_agent, "history"):
        health_agent.history = []

    # # Make the agent more strict about not making assumptions
    # modify_system_prompt()

    # Don't send welcome message immediately - wait for client to send "start_interview" message
    # This prevents audio from playing before user logs in

    # Main WebSocket loop
    try:
        while True:
            # Receive message from client
            message = await websocket.receive()

            # Handle text messages (JSON control messages)
            if "text" in message:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "")

                    if msg_type == "start_interview":
                        # Client is ready to start the interview (after login or page reload)
                        from src.prompts import WELCOME_PROMPT, READY_TO_START_PROMPT

                        # Get user_id from the message if provided
                        provided_user_id = data.get("userId")
                        if not provided_user_id:
                            # Frontend must always send a real application user ID.
                            # Without it, we cannot safely tie forms/attachments to users.
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Missing userId in start_interview. Please log in and retry.",
                                    }
                                )
                            )
                            continue

                        # Validate that the user exists in the database
                        if not validate_user_exists(provided_user_id):
                            # Get database info for error message
                            db_name = users_collection.database.name if users_collection else "unknown"
                            collection_name = users_collection.name if users_collection else "users"
                            error_msg = f"User {provided_user_id} does not exist in {db_name}.{collection_name}. Please use a valid user ID from your user management system."
                            print(f"[start_interview] ERROR: {error_msg}")
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": error_msg,
                                    }
                                )
                            )
                            continue

                        # Business rule: Form ID is fixed (same for all users).
                        # Uniqueness comes from the combination of (formId + userId).
                        # Each user has exactly one form identified by (DEFAULT_FORM_ID + userId).

                        client_state["user_id"] = provided_user_id
                        # Always use the fixed form ID
                        provided_form_id = DEFAULT_FORM_ID
                        client_state["form_id"] = provided_form_id
                            
                        # Check if user already has a form (resume mode)
                        existing_form = fetch_latest_form_for_user(provided_user_id)
                        if existing_form:
                            print(f"[start_interview] RESUME MODE: Found existing form for user {provided_user_id}.")
                            is_resuming = True
                        else:
                            # NEW INTERVIEW MODE: No existing form for this user, start fresh
                            print(f"[start_interview] NEW INTERVIEW MODE: No existing form for user {provided_user_id}. Resetting health_agent.")
                            reset_health_agent_for_new_interview(client_state, provided_user_id)
                            is_resuming = False
                        
                        # Handle form initialization based on mode (new vs resume)
                        if is_resuming:
                            # RESUME MODE: Load the existing form from MongoDB
                            print(f"[start_interview] Loading existing form {provided_form_id} (DEFAULT_FORM_ID) for user {provided_user_id} from MongoDB...")
                            
                            # Load the form from database (using formId + userId combination)
                            existing_form = fetch_form_by_id(provided_form_id, provided_user_id)
                            
                            if existing_form:
                                # Verify this form belongs to the current user (double-check)
                                form_user_id = existing_form.get("userId")
                                # Normalize to strings for comparison (ObjectId vs string)
                                if str(form_user_id) != str(provided_user_id):
                                    print(f"[start_interview] SECURITY ERROR: Form {provided_form_id} belongs to {form_user_id}, but current user is {provided_user_id}")
                                    await websocket.send_text(
                                        json.dumps({
                                            "type": "error",
                                            "message": "Access denied: This form belongs to a different user."
                                        })
                                    )
                                    continue
                                
                                # Load the form data into health_agent
                                form_data = existing_form.get("form_data", {})
                                current_section = existing_form.get("current_section", "Present Complaint")
                                
                                # Update health_agent with the loaded data
                                health_agent.form = form_data
                                health_agent.current_section = current_section
                                
                                # Re-evaluate completion to set idx and current_section accurately
                                pc_complete = health_agent.validator("Present Complaint")
                                pain_complete = health_agent.validator("Pain Assessment")
                                prev_complete = health_agent.validator("Previous Consultations")
                                hist_complete = health_agent.validator("History & Diagnostics")
                                goals_complete = health_agent.validator("Treatment Goals")
                                referral_complete = health_agent.validator("Referral")

                                def first_incomplete():
                                    if not pc_complete:
                                        return "Present Complaint"
                                    if not prev_complete:
                                        return "Previous Consultations"
                                    if not pain_complete:
                                        return "Pain Assessment"
                                    if not hist_complete:
                                        return "History & Diagnostics"
                                    if not goals_complete:
                                        return "Treatment Goals"
                                    if not referral_complete:
                                        return "Referral"
                                    return current_section

                                # Set idx based on which section group is incomplete.
                                # idx = 0  → Present Complaint + Previous Consultations
                                # idx = 1  → Pain Assessment + History & Diagnostics
                                # idx = 2  → Treatment Goals + Referral
                                # idx = 3  → All sections complete
                                if (not pc_complete) or (not prev_complete):
                                    health_agent.idx = 0
                                elif (not pain_complete) or (not hist_complete):
                                    health_agent.idx = 1
                                elif (not goals_complete) or (not referral_complete):
                                    health_agent.idx = 2
                                else:
                                    health_agent.idx = 3

                                # Set current_section to first incomplete
                                health_agent.current_section = first_incomplete()
                                
                                # Set talk_mode to USER since we're resuming
                                health_agent.talk_mode = "USER"
                                
                                print(f"[start_interview] ✓ Loaded form {provided_form_id}, section: {health_agent.current_section}, idx: {health_agent.idx}")
                                
                                # Decide whether we have enough information to show a full summary
                                filled_field_count = 0
                                for section, fields in health_agent.form.items():
                                    if isinstance(fields, dict):
                                        for _, value in fields.items():
                                            if value and str(value).strip():
                                                filled_field_count += 1

                                summary_text = None
                                MIN_FIELDS_FOR_SUMMARY = 5  # require a bit of data before showing a full summary

                                if filled_field_count >= MIN_FIELDS_FOR_SUMMARY:
                                    # Generate a summary of information collected so far
                                    try:
                                        summary_text = health_agent.generate_summary()
                                    except Exception as e:
                                        print(f"[start_interview] Error generating resume summary: {e}")
                                        import traceback
                                        traceback.print_exc()
                                        summary_text = None

                                # Build a resume message for the user.
                                if summary_text:
                                    # The summary itself already ends with a confirmation question,
                                    # so we don't append an extra "Ready to restart?" line here.
                                    resume_message = (
                                        "Welcome back, here's a quick summary of what I've noted till now:\n\n"
                                        f"{summary_text}"
                                    )
                                else:
                                    # Not enough data yet – don't show a long "empty" summary.
                                    # Just reassure the user and continue with the next questions.
                                    resume_message = (
                                        "Welcome back. We haven't collected much information yet, "
                                        "so let's continue with a few quick questions to understand your problem better."
                                    )
                                
                                await send_text_message(
                                    websocket,
                                    client_state,
                                    resume_message,
                                    build_interview_state(client_state),
                                    user_response=None
                                )
                                
                                # Ask the next question to continue the interview
                                prompt_template = health_agent.make_template(mode="query")
                                next_question = health_agent.talk_to_user(prompt_template)
                                
                                await send_text_message(
                                    websocket,
                                    client_state,
                                    next_question,
                                    build_interview_state(client_state),
                                    user_response=None
                                )
                                
                                # Skip the rest of the start_interview logic
                                continue
                            else:
                                print(f"[start_interview] ERROR: Form {provided_form_id} not found in database. Starting new interview instead.")
                                # Fall through to create a new form
                                is_resuming = False
                                reset_health_agent_for_new_interview(client_state, provided_user_id)
                        
                        # NEW INTERVIEW MODE: Verify reset and create new form
                        if not is_resuming:
                            # CRITICAL: Verify reset was successful - form should be empty
                            form_is_empty = True
                            for section, fields in health_agent.form.items():
                                if isinstance(fields, dict):
                                    for field, value in fields.items():
                                        if value and str(value).strip():
                                            form_is_empty = False
                                            print(f"[start_interview] ERROR: Form still has data after reset! {section}.{field} = {value}")
                            
                            if form_is_empty:
                                print(f"[start_interview] ✓ Interview started for user: {provided_user_id}, form is empty")
                            else:
                                print(f"[start_interview] ✗ WARNING: Form has data after reset! Forcing another reset...")
                                health_agent.init_form()
                                health_agent.talk_mode = "START"
                                health_agent.history = []
                                health_agent.history.append({"role": "agent", "message": health_agent.welcome_prompt})

                            # Create a new placeholder form
                            print(f"[start_interview] Creating new placeholder form for user: {client_state['user_id']}")
                            form_id = create_placeholder_form(
                                client_state["user_id"], client_state
                            )
                            # Verify the form was created with empty data
                            if form_id:
                                form_check = fetch_form_by_id(form_id, client_state["user_id"])
                                if form_check:
                                    form_data_check = form_check.get("form_data", {})
                                    # Log what was actually saved
                                    print(f"[start_interview] ✓ Created form {form_id}, verifying it's empty...")
                                    # Check if any fields have non-empty values
                                    has_data = False
                                    for section, fields in form_data_check.items():
                                        if isinstance(fields, dict):
                                            for field, value in fields.items():
                                                if value and str(value).strip():
                                                    has_data = True
                                                    print(f"[start_interview] WARNING: Found non-empty data in {section}.{field} = {value}")
                                    if not has_data:
                                        print(f"[start_interview] ✓ Form {form_id} verified empty")
                                    else:
                                        print(f"[start_interview] ✗ ERROR: Form {form_id} was created with existing data!")

                            # CRITICAL: Ensure health_agent.form is still empty before proceeding
                            # Check if health_agent.form has been contaminated after placeholder creation
                            form_has_data = False
                            for section, fields in health_agent.form.items():
                                if isinstance(fields, dict):
                                    for field, value in fields.items():
                                        if value and str(value).strip():
                                            form_has_data = True
                                            print(f"[start_interview] WARNING: health_agent.form has data before main_processor! {section}.{field} = {value}")
                            
                            if form_has_data:
                                print(f"[start_interview] CRITICAL: health_agent.form was contaminated! Resetting again...")
                                health_agent.init_form()
                            
                            # Send welcome message for new interview
                            # Only send welcome message if we're creating a new form (no formId provided)
                                welcome_text = WELCOME_PROMPT.strip()
                                # Add the "ready to start" question
                                welcome_text += "\n\n" + READY_TO_START_PROMPT.strip()
                                # After sending welcome, the next user response should move to USER mode
                                # Don't change talk_mode here - let main_processor handle it on first user input
                            else:
                                # If agent was already in a conversation, this shouldn't happen in start_interview
                                # But if it does, just use a generic message instead of calling main_processor("")
                                print(f"[start_interview] WARNING: talk_mode is {health_agent.talk_mode}, not START. Using welcome message.")
                                welcome_text = WELCOME_PROMPT.strip() + "\n\n" + READY_TO_START_PROMPT.strip()
                                # Reset to START mode to ensure proper flow
                                health_agent.talk_mode = "START"
                            
                            # After main_processor, check if form was modified (it shouldn't be for START mode)
                            if health_agent.talk_mode == "START":
                                form_has_data_after = False
                                for section, fields in health_agent.form.items():
                                    if isinstance(fields, dict):
                                        for field, value in fields.items():
                                            if value and str(value).strip():
                                                form_has_data_after = True
                                                print(f"[start_interview] WARNING: health_agent.form modified by main_processor! {section}.{field} = {value}")
                            
                            if form_has_data_after:
                                print(f"[start_interview] CRITICAL: main_processor modified form in START mode! Resetting form.")
                                health_agent.init_form()

                        # Calculate progress for interview state - use database form if available
                        # This ensures each formId is independent and not affected by other users
                        form_id_for_progress = client_state.get("form_id")
                        user_id_for_progress = client_state.get("user_id")
                        if form_id_for_progress and user_id_for_progress:
                            # Always use database form for accurate progress (ensures independence)
                            form_from_db = fetch_form_by_id(form_id_for_progress, user_id_for_progress)
                            if form_from_db and form_from_db.get("form_data"):
                                progress = calculate_form_progress(form_from_db["form_data"])
                            else:
                                progress = calculate_form_progress(health_agent.form)
                        else:
                            progress = calculate_form_progress(health_agent.form)

                        await send_text_message(
                            websocket,
                            client_state,
                            welcome_text,
                            build_interview_state(
                                client_state, progress_override=progress, use_db_form=True
                            ),
                            user_response=None
                        )

                    elif msg_type == "start_new_form":
                        # User wants to start a new form
                        from src.prompts import WELCOME_PROMPT, READY_TO_START_PROMPT
                        
                        # Ensure we have a valid user_id (may be provided in this message)
                        provided_user_id = data.get("userId")
                        if client_state.get("user_id") is None and provided_user_id:
                            client_state["user_id"] = provided_user_id
                            print(f"[start_new_form] Set user_id from message: {provided_user_id}")

                        if client_state.get("user_id") is None:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "message": "Cannot start new form without a userId. Please log in again.",
                                    }
                                )
                            )
                            continue

                        # Clear the old form_id from client_state to ensure a fresh start
                        old_form_id = client_state.get("form_id")
                        client_state["form_id"] = None
                        
                        # Reset the health agent for a new form (this clears form data)
                        reset_health_agent_for_new_interview(client_state, client_state["user_id"])
                        health_agent.talk_mode = "START"
                        
                        # Create a new placeholder form with fresh form_id
                        new_form_id = create_placeholder_form(client_state["user_id"], client_state)
                        
                        print(f"[start_new_form] Old form_id: {old_form_id}, New form_id: {new_form_id}")
                        print(f"[start_new_form] health_agent.form after reset - keys: {list(health_agent.form.keys())}")
                        
                        # Verify the new form is empty
                        if new_form_id:
                            form_check = fetch_form_by_id(new_form_id, client_state["user_id"])
                            if form_check:
                                form_data_check = form_check.get("form_data", {})
                                has_data = any(
                                    v and str(v).strip() if not isinstance(v, dict) 
                                    else any(val and str(val).strip() for val in v.values())
                                    for v in form_data_check.values()
                                )
                                if has_data:
                                    print(f"[start_new_form] ✗ ERROR: New form {new_form_id} has data!")
                                else:
                                    print(f"[start_new_form] ✓ Verified new form {new_form_id} is empty")
                        
                        welcome_text = WELCOME_PROMPT.strip()
                        welcome_text += "\n\n" + READY_TO_START_PROMPT.strip()
                        
                        # Use database form for progress (should be 0% for new empty form)
                        if new_form_id:
                            form_from_db = fetch_form_by_id(new_form_id, client_state["user_id"])
                            if form_from_db and form_from_db.get("form_data"):
                                progress = calculate_form_progress(form_from_db["form_data"])
                            else:
                                progress = calculate_form_progress(health_agent.form)
                        else:
                            progress = calculate_form_progress(health_agent.form)
                        
                        await send_text_message(
                            websocket,
                            client_state,
                            welcome_text,
                            build_interview_state(
                                client_state, progress_override=progress
                            ),
                            user_response=None
                        )

                    elif msg_type == "load_form":
                        # User wants to load an existing form
                        form_id = data.get("formId", "")
                        if not form_id:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form ID is required",
                                    }
                                )
                            )
                            continue
                        
                        # CRITICAL: Check if start_interview just created an empty form
                        # If so, we should delete it before loading the existing form to prevent orphaned forms
                        existing_form_id = client_state.get("form_id")
                        user_id_for_check = client_state.get("user_id")
                        if existing_form_id and existing_form_id != form_id and user_id_for_check:
                            # Check if the existing form is empty (just created by start_interview)
                            existing_form = fetch_form_by_id(existing_form_id, user_id_for_check)
                            if existing_form:
                                form_data_existing = existing_form.get("form_data", {})
                                is_empty = True
                                for section, fields in form_data_existing.items():
                                    if isinstance(fields, dict):
                                        for field, value in fields.items():
                                            if value and str(value).strip():
                                                is_empty = False
                                                break
                                        if not is_empty:
                                            break
                                
                                if is_empty:
                                    print(f"[load_form] Found empty form {existing_form_id} created by start_interview. Deleting it before loading existing form {form_id}.")
                                    # Delete the empty form to prevent orphaned forms in the database
                                    try:
                                        if customer_info_collection is not None:
                                            result = customer_info_collection.delete_one({"formId": existing_form_id})
                                            if result.deleted_count > 0:
                                                print(f"[load_form] ✓ Deleted empty form {existing_form_id}")
                                            else:
                                                print(f"[load_form] Warning: Empty form {existing_form_id} not found in database (may have been already deleted)")
                                        else:
                                            print(f"[load_form] Warning: MongoDB not initialized, cannot delete empty form {existing_form_id}")
                                    except Exception as e:
                                        print(f"[load_form] Error deleting empty form {existing_form_id}: {e}")
                                    # Clear the form_id from client_state so we use the new one
                                    client_state.pop("form_id", None)
                        
                        # Retrieve the form from database
                        user_id_for_load = client_state.get("user_id")
                        if not user_id_for_load:
                            await websocket.send_text(
                                json.dumps({
                                    "type": "error",
                                    "text": "User ID is required to load form",
                                })
                            )
                            continue
                        form = fetch_form_by_id(form_id, user_id_for_load)
                        if not form:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form not found",
                                    }
                                )
                            )
                            continue
                        
                        # CRITICAL: If user_id is not set yet, get it from the form
                        # This handles the case where load_form is called before start_interview
                        form_user_id = form.get("userId")
                        current_user_id = client_state.get("user_id")
                        
                        # If client_state doesn't have user_id, set it from the form
                        # (This handles load_form being called before start_interview)
                        if not current_user_id and form_user_id:
                            print(f"[load_form] Setting user_id from form: {form_user_id}")
                            client_state["user_id"] = form_user_id
                            current_user_id = form_user_id
                        
                        # Verify form belongs to current user (security check)
                        if form_user_id and current_user_id and form_user_id != current_user_id:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Form does not belong to current user",
                                    }
                                )
                            )
                            print(f"[load_form] Security check failed: form belongs to {form_user_id}, but current user is {current_user_id}")
                            continue
                        
                        # CRITICAL: Initialize/reset health_agent BEFORE loading form data to prevent data leakage
                        # This ensures no previous user's data contaminates this form load
                        # Also initialize user_id if not set
                        if not client_state.get("user_id"):
                            reset_health_agent_for_new_interview(client_state, form_user_id)
                        else:
                            health_agent.init_form()
                            health_agent.talk_mode = "START"
                            health_agent.history = []
                            health_agent.history.append({"role": "agent", "message": health_agent.welcome_prompt})
                        
                        # Load form data from database and DEEP COPY it to prevent shared references
                        import copy
                        form_data = form.get("form_data", {})
                        form_data_copy = copy.deepcopy(form_data)  # CRITICAL: Deep copy to prevent sharing
                        current_section = form.get("current_section", "")
                        
                        # Update health agent with the deep copied form data
                        health_agent.form = form_data_copy
                        health_agent.current_section = current_section
                        
                        # CRITICAL: Set talk_mode to USER if form has data (not a fresh form)
                        # This prevents the system from asking the initial question again
                        has_form_data = any(
                            isinstance(fields, dict) and any(str(v).strip() for v in fields.values())
                            for fields in form_data_copy.values()
                        )
                        if has_form_data:
                            health_agent.talk_mode = "USER"
                            print(f"[load_form] Form {form_id} has existing data, setting talk_mode to USER")
                        
                        # Determine the correct idx AND set current_section to first incomplete section
                        # idx=0: Present Complaint and Pain Assessment
                        # idx=1: Previous Consultations and History & Diagnostics
                        # idx=2: Treatment Goals and Referral
                        present_complaint_complete = health_agent.validator("Present Complaint")
                        pain_assessment_complete = health_agent.validator("Pain Assessment")
                        prev_consultations_complete = health_agent.validator("Previous Consultations")
                        history_diag_complete = health_agent.validator("History & Diagnostics")
                        treatment_goals_complete = health_agent.validator("Treatment Goals")
                        referral_complete = health_agent.validator("Referral")

                        # Helper to set current_section to the first incomplete section in order
                        def set_first_incomplete_section():
                            if not present_complaint_complete:
                                return "Present Complaint"
                            if not pain_assessment_complete:
                                return "Pain Assessment"
                            if not prev_consultations_complete:
                                return "Previous Consultations"
                            if not history_diag_complete:
                                return "History & Diagnostics"
                            if not treatment_goals_complete:
                                return "Treatment Goals"
                            if not referral_complete:
                                return "Referral"
                            return health_agent.current_section  # all complete, keep as-is

                        # Determine idx based on completion status
                        if present_complaint_complete and pain_assessment_complete:
                            if prev_consultations_complete and history_diag_complete:
                                if treatment_goals_complete and referral_complete:
                                    # All sections complete - interview is done
                                    health_agent.idx = 3  # Beyond last predefined question
                                else:
                                    # On idx=2 (Treatment Goals and Referral)
                                    health_agent.idx = 2
                            else:
                                # On idx=1 (Previous Consultations and History & Diagnostics)
                                health_agent.idx = 1
                        else:
                            # On idx=0 (Present Complaint and Pain Assessment)
                            health_agent.idx = 0

                        # Set current_section to the first incomplete section so progress matches questions
                        health_agent.current_section = set_first_incomplete_section()
                        print(f"[load_form] Set current_section to first incomplete section: {health_agent.current_section}, idx: {health_agent.idx}")
                        
                        print(f"[load_form] Restored idx={health_agent.idx} based on section completion status")
                        
                        print(f"[load_form] Loaded form {form_id} for user {current_user_id} (deep copied, talk_mode={health_agent.talk_mode}, idx={health_agent.idx})")
                        
                        # Store form_id in client state for saving
                        client_state["form_id"] = form_id
                        
                        # Calculate progress using the deep copied data
                        progress = calculate_form_progress(form_data_copy)
                        
                        # Generate the next question or summary that should be shown
                        next_message = None
                        is_complete = (present_complaint_complete and pain_assessment_complete and 
                                      prev_consultations_complete and history_diag_complete and 
                                      treatment_goals_complete and referral_complete)
                        
                        if has_form_data:
                            if is_complete:
                                # Form is complete - show summary for confirmation
                                try:
                                    summary = health_agent.generate_summary()
                                    health_agent.conversation_state['awaiting_summary_confirmation'] = True
                                    health_agent.history.append({"role": "agent", "message": summary})
                                    next_message = summary
                                    print(f"[load_form] Form is complete, generated summary for confirmation")
                                except Exception as e:
                                    print(f"[load_form] Error generating summary: {e}")
                                    import traceback
                                    traceback.print_exc()
                            else:
                                # Form has data but is not complete - generate next question
                                try:
                                    # Try to generate question for current section
                                    prompt_template = health_agent.make_template(mode="query")
                                    if prompt_template == "SKIP_SECTION":
                                        # Current section should be skipped, move to next section
                                        print(f"[load_form] Current section {health_agent.current_section} should be skipped, moving to next")
                                        # Move to next section
                                        current_index = health_agent.form_sections.index(health_agent.current_section)
                                        if current_index < len(health_agent.form_sections) - 1:
                                            health_agent.current_section = health_agent.form_sections[current_index + 1]
                                            # Try generating question for new section
                                            prompt_template = health_agent.make_template(mode="query")
                                    
                                    if prompt_template and prompt_template != "SKIP_SECTION":
                                        next_message = health_agent.talk_to_user(prompt_template)
                                        # Add to history
                                        health_agent.history.append({"role": "agent", "message": next_message})
                                        print(f"[load_form] Generated next question: {next_message[:100]}...")
                                    else:
                                        print(f"[load_form] Could not generate question (section may be complete or skipped)")
                                except Exception as e:
                                    print(f"[load_form] Error generating next question: {e}")
                                    import traceback
                                    traceback.print_exc()
                        
                        # Send confirmation and current state (use deep copy, not original)
                        response_data = {
                            "type": "form_loaded",
                            "text": f"Loaded form: {form.get('title', 'Untitled Form')}",
                            "session_id": client_state["session_id"],
                            "interview_state": build_interview_state(
                                client_state, progress_override=progress
                            ),
                            "form_data": form_data_copy,  # Use deep copy
                        }
                        
                        await websocket.send_text(json.dumps(response_data))
                        
                        # Send the question/summary as the primary message (replaces "Loaded form" in UI)
                        if next_message:
                            # Send the question/summary as a text message (same format as normal interview flow)
                            await send_text_message(
                                websocket,
                                client_state,
                                next_message,
                                interview_state=build_interview_state(client_state, progress_override=progress),
                                user_response=None
                            )
                            print(f"[load_form] Sent next question/summary to user: {next_message[:100]}...")
                        else:
                            # If no question could be generated, at least send a message indicating we're ready
                            await send_text_message(
                                websocket,
                                client_state,
                                "Welcome back! Let's continue with your medical interview.",
                                interview_state=build_interview_state(client_state, progress_override=progress),
                                user_response=None
                            )
                        
                        # Continue from where they left off

                    elif msg_type == "end_session":
                        provided_user_id = data.get("userId")
                        if provided_user_id:
                            client_state["user_id"] = provided_user_id

                        try:
                            import copy
                            health_agent.save_progress()
                            form_id = client_state.get("form_id")
                            save_customer_info(
                                user_id=client_state["user_id"],
                                form_data=copy.deepcopy(health_agent.form),  # Deep copy to prevent sharing
                                current_section=health_agent.current_section,
                                form_id=form_id
                            )
                            await send_text_message(
                                websocket,
                                client_state,
                                "Interview summary saved.",
                                build_interview_state(
                                    client_state,
                                    progress_override=calculate_form_progress(
                                        health_agent.form
                                    ),
                                ),
                                user_response=None
                            )
                        except Exception as e:
                            print(f"Error saving customer info on end_session: {e}")

                    elif msg_type == "audio_start":
                        # Client is starting to send audio
                        client_state["is_recording"] = True
                        client_state["received_audio_buffer"] = bytearray()
                        client_state["recording_start_time"] = data.get(
                            "timestamp", time.time()
                        )
                        print("Client started sending audio")

                    elif msg_type == "text_input":
                        # Client is sending text input (manual or auto-sent transcription)
                        text_input = data.get("text", "").strip()
                        if not text_input:
                            continue
                        
                        print(f"Received text input: {text_input}")
                        print(f"[text_input] Current talk_mode: {health_agent.talk_mode}, history length: {len(health_agent.history)}")
                        
                        # Process the text input with HealthAgent (same as transcription)
                        try:
                            # CRITICAL: Ensure talk_mode is correct before processing
                            # If talk_mode is START and we have user input, it should process normally
                            # main_processor will change talk_mode from START to USER on first real input
                            response_text = health_agent.main_processor(text_input)
                            print(f"[text_input] After processing, talk_mode: {health_agent.talk_mode}, history length: {len(health_agent.history)}")

                            # CRITICAL: Handle SKIP_SECTION response - move to next section and generate new question
                            if response_text == "SKIP_SECTION":
                                print(f"[text_input] Received SKIP_SECTION, moving to next section and generating new question")
                                # Move to next section
                                current_index = health_agent.form_sections.index(health_agent.current_section)
                                if current_index < len(health_agent.form_sections) - 1:
                                    health_agent.current_section = health_agent.form_sections[current_index + 1]
                                    # Generate question for new section
                                    prompt_template = health_agent.make_template(mode="query")
                                    if prompt_template and prompt_template != "SKIP_SECTION":
                                        response_text = health_agent.talk_to_user(prompt_template)
                                        health_agent.history.append({"role": "agent", "message": response_text})
                                    else:
                                        # If still SKIP_SECTION or None, try to move forward or complete
                                        response_text = "Let me move on to the next section."
                                else:
                                    # All sections complete
                                    response_text = "Thank you for providing all the information. Let me generate a summary."

                            # Check if form is complete
                            is_complete = "Thank you for completing all questions" in response_text

                            # Check if we should save the form state
                            if health_agent.talk_mode != "START":
                                health_agent.save_progress()
                                # Also save to MongoDB customer-info collection
                                form_id = client_state.get("form_id")
                                user_id = client_state.get("user_id")
                                
                                # CRITICAL: Verify form_id belongs to this user before saving
                                if form_id and user_id:
                                    existing_form = fetch_form_by_id(form_id, user_id)
                                    if existing_form:
                                        form_user_id = existing_form.get("userId")
                                        # Normalize to strings for comparison (ObjectId vs string)
                                        if str(form_user_id) != str(user_id):
                                            print(f"[text_input] SECURITY ERROR: Form {form_id} belongs to {form_user_id}, but current user is {user_id}")
                                            # Don't save - this is a security issue
                                            continue
                                
                                # Deep copy health_agent.form before saving to prevent shared references
                                import copy
                                form_data_to_save = copy.deepcopy(health_agent.form)
                                
                                saved_form_id = save_customer_info(
                                    user_id=user_id,
                                    form_data=form_data_to_save,  # Use deep copy
                                    current_section=health_agent.current_section,
                                    form_id=form_id
                                )
                                # Store form_id if it's a new form
                                if saved_form_id and not form_id:
                                    client_state["form_id"] = saved_form_id
                                    print(f"[text_input] Created new form_id: {saved_form_id} for user: {user_id}")

                            # Calculate progress based on actual form data completion
                            # If form is complete, set progress to 100%
                            if is_complete:
                                progress = 100.0
                            else:
                                # Use database form for accurate progress (especially after uploads)
                                form_id = client_state.get("form_id")
                                user_id = client_state.get("user_id")
                                if form_id and user_id:
                                    form = fetch_form_by_id(form_id, user_id)
                                    if form and form.get("form_data"):
                                        progress = calculate_form_progress(form["form_data"])
                                    else:
                                        progress = calculate_form_progress(health_agent.form)
                                else:
                                    progress = calculate_form_progress(health_agent.form)

                            await send_text_message(
                                websocket,
                                client_state,
                                response_text,
                                build_interview_state(
                                    client_state, progress_override=progress
                                ),
                                user_response=text_input,  # Pass user's response to check if they have reports
                            )

                        except Exception as e:
                            print(f"Error processing text input: {e}")
                            await websocket.send_text(
                                json.dumps(
                                    {"type": "error", "text": f"Error: {str(e)}"}
                                )
                            )

                    elif msg_type == "audio_end":
                        # Client has finished sending audio
                        client_state["is_recording"] = False

                        # Process the accumulated audio
                        audio_bytes = client_state["received_audio_buffer"]
                        audio_duration = data.get("duration", 0)
                        print(
                            f"Received complete audio: {len(audio_bytes)} bytes, duration: {audio_duration:.2f}s"
                        )

                        # Skip processing if not enough audio data received
                        if (
                            len(audio_bytes) < RATE * SAMPLE_WIDTH * 0.5
                        ):  # At least 0.5 seconds
                            print(
                                f"Audio too short ({len(audio_bytes)} bytes), skipping"
                            )
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error",
                                        "text": "Audio too short to process",
                                    }
                                )
                            )
                            continue

                        # Save received audio for debugging (raw format)
                        timestamp = int(time.time())
                        raw_file_path = f"audio_files/server_received_{timestamp}.raw"
                        with open(raw_file_path, "wb") as f:
                            f.write(audio_bytes)
                        print(f"Saved raw audio to {raw_file_path} ({len(audio_bytes)} bytes)")

                        # Process audio with Whisper
                        try:
                            # The frontend sends WebM/Opus format, not raw PCM
                            # We need to convert it to a format Whisper can understand
                            # Use pydub to load and convert the audio
                            audio_segment = None
                            audio_format = None
                            
                            # Try different formats in order of likelihood
                            formats_to_try = [
                                ("webm", "WebM/Opus"),
                                ("ogg", "OGG/Opus"),
                                ("wav", "WAV"),
                                ("mp4", "MP4"),
                            ]
                            
                            for fmt, fmt_name in formats_to_try:
                                try:
                                    audio_segment = AudioSegment.from_file(
                                        io.BytesIO(audio_bytes),
                                        format=fmt
                                    )
                                    audio_format = fmt_name
                                    print(f"✅ Successfully loaded audio as {fmt_name}")
                                    break
                                except Exception as e:
                                    print(f"⚠️ Failed to load as {fmt_name}: {e}")
                                    continue
                            
                            if audio_segment is None:
                                raise Exception("Could not determine audio format. Please ensure ffmpeg is installed.")
                            
                            # Convert to mono, 16kHz, 16-bit (Whisper requirements)
                            audio_segment = audio_segment.set_channels(1)
                            audio_segment = audio_segment.set_frame_rate(RATE)
                            audio_segment = audio_segment.set_sample_width(2)  # 16-bit
                            
                            # Export to raw bytes
                            wav_io = io.BytesIO()
                            audio_segment.export(wav_io, format="wav")
                            wav_data = wav_io.getvalue()
                            
                            # Save converted WAV for debugging
                            wav_file_path = f"audio_files/server_received_{timestamp}.wav"
                            with open(wav_file_path, "wb") as f:
                                f.write(wav_data)
                            print(f"Saved converted WAV to {wav_file_path}")
                            
                            # Convert to numpy array for Whisper
                            # Skip WAV header (first 44 bytes) and get raw audio data
                            audio_data = audio_segment.get_array_of_samples()
                            np_audio = np.array(audio_data, dtype=np.float32) / 32768.0

                            # Skip if audio appears to be empty or corrupted
                            if np_audio.size == 0 or np.max(np.abs(np_audio)) < 0.01:
                                print(
                                    "⚠️ Audio seems silent or corrupted, skipping transcription"
                                )
                                await websocket.send_text(
                                    json.dumps(
                                        {
                                            "type": "error",
                                            "text": "Audio too quiet or corrupted",
                                        }
                                    )
                                )
                                continue
                            
                            print(f"Audio processed: {len(np_audio)} samples, max amplitude: {np.max(np.abs(np_audio)):.4f}")

                            # Process with Whisper without trimming to 30s
                            transcribe_options = {
                                "language": "en",
                                "fp16": torch.cuda.is_available() and cuda_device == "cuda",
                                "temperature": 0,
                            }
                            result = whisper.transcribe(
                                model,
                                np_audio,
                                **transcribe_options,
                            )

                            # Extract transcription
                            transcription = result.get("text", "").strip()
                            print(f"✅ Transcription: {transcription}")

                            # Send transcription immediately to frontend for real-time display
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "transcription",
                                        "text": transcription,
                                        "timestamp": timestamp,
                                    }
                                )
                            )

                            # Save transcript for reference
                            transcript_path = f"transcripts/transcript_{timestamp}.txt"
                            with open(transcript_path, "w") as f:
                                f.write(transcription)

                            # Log user's speech in the database
                            # MongoDB DISABLED - Commented out
                            # db.log_interaction(
                            #     user_id=client_state["user_id"],
                            #     interview_id=client_state["interview_id"],
                            #     interaction_type="user_speech",
                            #     content=transcription,
                            #     metadata={
                            #         "audio_duration": audio_duration,
                            #         "audio_file": file_path,
                            #         "transcript_file": transcript_path,
                            #     },
                            # )

                            # DO NOT process transcription automatically here
                            # Wait for user to click send button, which will send a text_input message
                            # The text_input handler (line 751) will process it and send the response
                            print(f"Transcription sent to frontend. Waiting for user to click send...")

                        except Exception as e:
                            import traceback
                            error_details = traceback.format_exc()
                            print(f"❌ Error processing audio: {e}")
                            print(f"Error details:\n{error_details}")
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "type": "error", 
                                        "text": f"Error processing audio: {str(e)}. Please ensure ffmpeg is installed for audio conversion."
                                    }
                                )
                            )

                except json.JSONDecodeError:
                    print(f"Error decoding JSON: {message['text'][:100]}...")
                except Exception as e:
                    print(f"Error handling text message: {e}")

            # Handle binary messages (audio data)
            elif "bytes" in message:
                if client_state["is_recording"]:
                    # Accumulate audio chunks
                    audio_chunk = message["bytes"]
                    client_state["received_audio_buffer"].extend(audio_chunk)

                    # Log progress
                    total_kb = len(client_state["received_audio_buffer"]) / 1024
                    chunk_kb = len(audio_chunk) / 1024
                    print(
                        f"Received audio chunk: {chunk_kb:.1f} KB, total: {total_kb:.1f} KB"
                    )
                else:
                    print("Received unexpected binary data when not recording")

    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        print("Client disconnected")
        # Close database connection
        # MongoDB DISABLED - Commented out
        # db.close()


def validate_user_exists(user_id: str) -> bool:
    """
    Validate that a user exists in the MongoDB users collection.
    
    Args:
        user_id: The user ID to validate (as string or ObjectId)
        
    Returns:
        True if user exists, False otherwise
    """
    try:
        from bson import ObjectId
        
        # Ensure MongoDB connection
        if users_collection is None:
            print(f"[validate_user_exists] MongoDB not connected")
            return False
        
        # Get database and collection names for logging
        db_name = users_collection.database.name
        collection_name = users_collection.name
        
        # Convert user_id to ObjectId if it's a valid ObjectId string
        try:
            user_object_id = ObjectId(user_id) if isinstance(user_id, str) else user_id
        except Exception:
            print(f"[validate_user_exists] Invalid user_id format: {user_id}")
            return False
        
        # Check if user exists
        user = users_collection.find_one({"_id": user_object_id})
        exists = user is not None
        
        if exists:
            print(f"[validate_user_exists] ✓ User {user_id} exists in {db_name}.{collection_name}")
        else:
            print(f"[validate_user_exists] ✗ User {user_id} NOT FOUND in {db_name}.{collection_name}")
        
        return exists
        
    except Exception as e:
        print(f"[validate_user_exists] Error validating user {user_id}: {e}")
        import traceback
        traceback.print_exc()
        return False


def ensure_users_collection() -> Collection:
    """Get an initialized users collection or raise."""
    global users_collection

    if users_collection is None:
        # Attempt to reconnect once to avoid stale connections
        init_mongo()

    if users_collection is None:
        raise RuntimeError("User directory is not configured on the server.")

    return users_collection


@app.get("/api/users")
async def get_users(
    search: str = Query(default="", description="Optional search term"),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Return up-to-date list of users for the selector."""
    try:
        collection = ensure_users_collection()

        mongo_query = {}
        if search:
            regex = {"$regex": search, "$options": "i"}
            mongo_query = {
                "$or": [
                    {"firstName": regex},
                    {"lastName": regex},
                    {"name": regex},
                    {"email": regex},
                ]
            }

        cursor = (
            collection.find(mongo_query)
            .sort("updatedAt", -1)
            .limit(limit)
        )
        users = []
        for doc in cursor:
            serialized = serialize_user(doc)
            if serialized and serialized["id"]:
                users.append(serialized)

        return {"users": users}
    except RuntimeError as err:
        raise HTTPException(
            status_code=500,
            detail=str(err),
        ) from err
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch users: {str(e)}"
        )


@app.get("/api/users/{user_id}/forms")
async def get_user_forms_endpoint(user_id: str):
    """Return all forms for a specific user."""
    try:
        forms = fetch_user_forms(user_id)
        return {"forms": forms}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch user forms: {str(e)}"
        )


@app.get("/api/forms/{form_id}")
async def get_form_endpoint(form_id: str, userId: str = Query(..., description="User ID (required)")):
    """Return a specific form by form_id and userId."""
    try:
        form = fetch_form_by_id(form_id, userId)
        if form is None:
            raise HTTPException(status_code=404, detail="Form not found")
        return {"form": form}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch form: {str(e)}"
        )


@app.get("/api/forms/{form_id}/progress")
async def get_form_progress_endpoint(form_id: str, userId: str = Query(..., description="User ID (required)")):
    """
    Return section completion status for a form.
    Sections are ordered with completed sections first, incomplete sections at the bottom.
    """
    try:
        form = fetch_form_by_id(form_id, userId)
        if form is None:
            raise HTTPException(status_code=404, detail="Form not found")
        
        form_data = form.get("form_data", {})
        progress_data = calculate_section_completion_status(form_data)
        
        return progress_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to calculate form progress: {str(e)}"
        )


@app.post("/api/forms/{form_id}/attachments")
async def upload_form_attachment(
    form_id: str,
    files: List[UploadFile] = File(...),
    userId: str = Form(...),
):
    """Upload multiple scans/reports to S3, generate combined summary, and auto-fill Reports section."""
    if not is_s3_configured():
        raise HTTPException(
            status_code=500,
            detail="S3 is not configured on the server.",
        )

    form = fetch_form_by_id(form_id, userId)
    if not form:
        raise HTTPException(status_code=404, detail="Form not found.")

    # Enforce that each form remains bound to a single user.
    # This guarantees that every user has their own independent form record
    # and attachments, even if the *same* physical document is uploaded for
    # many different users (each upload will go to that user's own form).
    if form.get("userId") != userId:
        raise HTTPException(
            status_code=403,
            detail="Form does not belong to this user.",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    max_bytes = MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
    file_data_list = []
    attachment_records = []

    # Upload all files to S3 first
    for file in files:
        file_bytes = await file.read()
        if not file_bytes:
            continue

        if len(file_bytes) > max_bytes:
            raise HTTPException(
                status_code=400,
                detail=f"File {file.filename} exceeds {MAX_ATTACHMENT_SIZE_MB} MB limit.",
            )

        content_type = file.content_type or "application/octet-stream"
        key = generate_object_key(userId, form_id, file.filename or "upload.bin")
        
        try:
            s3_url = upload_bytes_to_s3(file_bytes, key, content_type)
            print(f"[upload_attachment] Uploaded {file.filename} to S3: {s3_url}")
        except RuntimeError as exc:
            print(f"[upload_attachment] S3 upload failed for {file.filename}: {exc}")
            continue

        file_data_list.append((file_bytes, file.filename or key.split("/")[-1]))
        
        attachment_records.append({
            "id": str(uuid.uuid4()),
            "type": "report",
            "label": "Medical Report",
            "fileName": file.filename or key.split("/")[-1],
            "url": s3_url,
            "uploadedAt": datetime.now(timezone.utc).isoformat(),
            "contentType": content_type,
        })

    if not file_data_list:
        raise HTTPException(status_code=400, detail="No valid files were uploaded.")

    # Generate combined summary from all documents
    from docscanner.service import summarize_multiple_reports
    
    combined_summary = None
    try:
        combined_summary = summarize_multiple_reports(file_data_list)
        print(f"[upload_attachment] Generated combined summary for {len(file_data_list)} documents")
    except Exception as exc:
        print(f"[upload_attachment] Doc-scanner failed: {exc}")
        import traceback
        traceback.print_exc()
        combined_summary = {"error": str(exc)}

    # Add summary to all attachment records
    for record in attachment_records:
        record["summary"] = combined_summary

    # Update MongoDB with attachments
    if customer_info_collection is None:
        init_mongo()

    if customer_info_collection is None:
        raise HTTPException(status_code=500, detail="Database unavailable.")

    # Add all attachments
    customer_info_collection.update_one(
        {"formId": form_id},
        {"$push": {"attachments": {"$each": attachment_records}}},
    )

    # Auto-fill Reports section in form_data
    form_data = form.get("form_data", {})
    if "Diagnostic Reports" not in form_data:
        form_data["Diagnostic Reports"] = {}
    
    # Create a readable summary text for the Reports field
    reports_text = ""
    if combined_summary and not combined_summary.get("error"):
        findings = combined_summary.get("findings", [])
        impression = combined_summary.get("impression", "")
        measurements = combined_summary.get("measurements", [])
        
        if findings:
            reports_text += "Findings:\n"
            for finding in findings:
                title = finding.get("title", "")
                details = finding.get("details", "")
                if title:
                    reports_text += f"- {title}: {details}\n"
                elif details:
                    reports_text += f"- {details}\n"
        
        if measurements:
            reports_text += "\nMeasurements:\n"
            for meas in measurements:
                label = meas.get("label", "")
                value = meas.get("value", "")
                units = meas.get("units", "")
                location = meas.get("anatomical_location", "")
                if label and value:
                    meas_text = f"- {label}: {value}"
                    if units:
                        meas_text += f" {units}"
                    if location:
                        meas_text += f" ({location})"
                    reports_text += meas_text + "\n"
        
        if impression:
            reports_text += f"\nImpression: {impression}\n"
        
        # Also store the full JSON summary
        reports_text += f"\n[Full summary available in attachment metadata]"
    else:
        reports_text = f"Uploaded {len(attachment_records)} document(s). "
        if combined_summary and combined_summary.get("error"):
            reports_text += f"Summary generation failed: {combined_summary.get('error')}"
        else:
            reports_text += "Summary processing in progress."

    form_data["Diagnostic Reports"]["Reports"] = reports_text.strip()

    # Update form_data in MongoDB
    customer_info_collection.update_one(
        {"formId": form_id},
        {
            "$set": {
                "form_data": form_data,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )

    # Sync health_agent.form with the updated database form so progress calculation is accurate
    # Only sync if this form_id matches the current client_state form_id
    # We'll sync in the response or when needed
    print(f"[upload_attachment] Updated form_data Reports section with combined summary")
    
    # Calculate progress from the updated form data
    updated_progress = calculate_form_progress(form_data)
    # Also calculate section completion status for frontend
    section_progress = calculate_section_completion_status(form_data)

    return {
        "attachments": attachment_records,
        "combined_summary": combined_summary,
        "reports_filled": True,
        "form_data": form_data,  # Return updated form data
        "progress": updated_progress,  # Return updated progress
        "sectionProgress": section_progress,  # Return section completion status
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8082)
