# Public API Contract (pre-refactor snapshot)

This file is the source of truth for what behaviour the refactor MUST preserve.
Any deviation from this surface is a regression.

Generated from `server.py` and `src/llm/functionalities.py` as of the snapshot
date below. Update only when intentional, externally-visible changes ship.

Snapshot date: 2026-05-26
Snapshot SHA (server.py): `144152 bytes` (matches EC2 host)

---

## 1. HTTP routes (FastAPI)

| Method | Path                                   | Handler                          | Notes                                              |
|--------|----------------------------------------|----------------------------------|----------------------------------------------------|
| GET    | `/health`                              | `health_check`                   | Liveness probe used by Docker healthcheck.         |
| GET    | `/api/users`                           | `get_users`                      | User directory listing for the dashboard.          |
| GET    | `/api/users/{user_id}/forms`           | `get_user_forms_endpoint`        | All forms for one user.                            |
| GET    | `/api/forms/{form_id}`                 | `get_form_endpoint`              | Requires `userId` query param.                     |
| GET    | `/api/forms/{form_id}/progress`        | `get_form_progress_endpoint`     | Requires `userId` query param.                     |
| POST   | `/api/forms/{form_id}/attachments`     | `upload_form_attachment`         | Multipart upload, S3-backed.                       |

CORS origins (must remain identical):
- `https://customerai.stance.health`
- `https://customer-agent-mu.vercel.app`
- `http://localhost:3000`, `:8000`, `:8080`, `:8081`

## 2. WebSocket: `/ws/{client_id}`

### Inbound message types (client → server)

| `type`            | Payload fields                                  | Purpose                                  |
|-------------------|-------------------------------------------------|------------------------------------------|
| `start_interview` | (client_state)                                  | Begin a fresh interview session.         |
| `start_new_form`  | (client_state, optional flags)                  | Start a brand-new form for the user.     |
| `load_form`       | `form_id`                                       | Resume an existing form.                 |
| `text_input`      | `text`                                          | User typed message (instead of voice).   |
| `audio_start`     | (signals upcoming chunks)                       | Beginning of streamed audio.             |
| `audio_chunk`     | `data` (base64 PCM/webm bytes)                  | One chunk of streamed audio.             |
| `audio_end`       | (none)                                          | Triggers Whisper transcription.          |
| `end_session`     | (none)                                          | Clean shutdown.                          |

### Outbound message types (server → client)

| `type`         | Fields                                                 | Sent when                                  |
|----------------|--------------------------------------------------------|--------------------------------------------|
| `text_message` | `text`, `message_id`                                   | Agent textual response.                    |
| `transcription`| `text`, `timestamp`                                    | After Whisper finishes on `audio_end`.     |
| `audio_start`  | `message_id`, `total_size`                             | TTS audio streaming begins.                |
| `audio_chunk`  | `message_id`, `data` (base64), `chunk_index`           | One chunk of TTS audio.                    |
| `form_loaded`  | (form payload)                                         | After `load_form` succeeds.                |
| `report`       | (summary payload)                                      | Final interview summary / report.          |
| `error`        | `text`                                                 | Any handler error path.                    |

## 3. `HealthAgent` public surface (`src/llm/functionalities.py`)

After refactor, `HealthAgent` must keep these methods callable with the same
signatures from `server.py`. Internals can move freely.

```
HealthAgent()                                    # ctor with no args
HealthAgent.ensure_string(text) -> str
HealthAgent.llm_complete(prompt) -> str
HealthAgent.start_keyword_thread()
HealthAgent.init_prompts()
HealthAgent.init_chromadb()
HealthAgent.init_health_info_index()
HealthAgent.init_chat_components()
HealthAgent.init_form()
HealthAgent.check_for_keyword()
HealthAgent.describe_action(text_chunk, threshold=0.5)
HealthAgent.formatter(user_input, prompt_template)
HealthAgent.extract_json_from_response(response)
HealthAgent.classify_summary_response(user_input: str) -> dict
HealthAgent.classify_reports_intent(user_input: str) -> dict
HealthAgent.should_check_for_correction(user_input: str) -> bool
HealthAgent.detect_correction(user_message, is_summary_mode=False)
HealthAgent.generate_summary() -> str
HealthAgent.apply_correction(correction_data, is_summary_mode=False)
HealthAgent.validator(current_section=None)
HealthAgent.all_ops()
HealthAgent.save_progress()
HealthAgent.talk_to_user(prompt_template)
HealthAgent.invalid_index()
HealthAgent.get_all_missing_fields()
HealthAgent.make_template(mode, source=None, context=None)
HealthAgent.main_processor(user_response)
```

Private (`_` prefix) — internal, but should still keep behaviour:
- `_is_semantically_compatible`
- `_propagate_correction`

## 4. Module-level globals consumed across files

These globals exist in `server.py` and are read/written across handlers.
They must remain accessible (or be replaced by an equivalent abstraction):

- `health_agent: HealthAgent`        — singleton, initialized at boot
- `model: whisper.Whisper`           — STT model
- `mongo_client`, `users_collection`, `customer_info_collection`
- `RATE` (16000), `SAMPLE_WIDTH` (2), `MAX_CHUNK_SIZE` (65536)
- `ALLOWED_ATTACHMENT_TYPES`, `MAX_ATTACHMENT_SIZE_MB`, `UPLOAD_TRIGGER_PHRASE`
- `MONGO_URI`, `MONGO_DB_NAME`, `MONGO_USERS_COLLECTION`,
  `MONGO_CUSTOMER_INFO_COLLECTION`

## 5. External effects (do not change)

- Whisper model: `base`, CPU/CUDA auto-selected.
- TTS: gTTS via `gtts` lib, cached under `tts_cache/`, 1.7× speed factor.
- ChromaDB persisted at `$CHROMA_DB_PATH` (default `/app/db/vector/...`).
- S3 uploads via `upload.s3_client` (presigned + bytes upload).
- MongoDB collections: `users`, `customer-info`.
- Filesystem writes (debug-only, will be flagged off in refactor):
  - `audio_files/server_received_<ts>.{raw,wav}`
  - `transcripts/transcript_<ts>.txt`
