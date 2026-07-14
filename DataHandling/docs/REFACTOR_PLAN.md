# Refactor plan

Goal: take `server.py` (1947 active lines) and `src/llm/functionalities.py`
(3970 lines) and reshape them into small, single-purpose modules — without
changing any externally observable behaviour (see `PUBLIC_API.md`).

## Guardrails

1. The contract in `PUBLIC_API.md` is frozen. Any change to it is intentional.
2. Each step below is one commit. If a step breaks something, revert just
   that commit — don't try to "fix forward".
3. The original files stay in place until the step that replaces them
   passes the smoke test.
4. No optimisation / library swaps in this phase. Cost work is a separate
   pass once the structure is clean.

## Target directory layout

```
DataHandling/
├── server.py                      # slim entrypoint, mounts routers (~80 lines)
├── app/
│   ├── config.py                  # env vars, paths, constants
│   ├── logging.py                 # logger setup
│   ├── db/
│   │   ├── mongo.py               # connection + collection accessors
│   │   └── serializers.py         # _serialize_datetime, serialize_user
│   ├── forms/
│   │   ├── repository.py          # save/fetch/create form CRUD
│   │   ├── progress.py            # progress + section completion math
│   │   ├── attachments.py         # fetch_form_attachments, upload helpers
│   │   └── titles.py              # generate_form_title, build_interview_state
│   ├── audio/
│   │   ├── tts.py                 # text_to_speech + cache (with eviction)
│   │   ├── stt.py                 # whisper wrapper
│   │   ├── streaming.py           # stream_audio_to_client
│   │   └── files.py               # save_audio (gated by DEBUG_AUDIO)
│   ├── interview/
│   │   ├── helpers.py             # user_has_reports, message_requires_attachment
│   │   ├── messaging.py           # send_text_message
│   │   └── lifecycle.py           # reset_health_agent_for_new_interview
│   ├── ws/
│   │   ├── handler.py             # websocket_endpoint shell
│   │   ├── stages/                # the 1044-line WS split into stages
│   │   │   ├── start_interview.py
│   │   │   ├── start_new_form.py
│   │   │   ├── load_form.py
│   │   │   ├── audio_pipeline.py
│   │   │   ├── text_input.py
│   │   │   └── end_session.py
│   │   └── protocol.py            # message type constants + payload helpers
│   └── routes/
│       ├── health.py
│       ├── users.py
│       └── forms.py
└── src/
    └── agent/                     # was src/llm/functionalities.py
        ├── __init__.py            # exposes HealthAgent (unchanged public API)
        ├── core.py                # HealthAgent class — composes services
        ├── init_services.py       # chroma, llama-index, prompts wiring
        ├── llm_utils.py           # ensure_string, llm_complete, formatter, json
        ├── classify.py            # summary + reports intent classification
        ├── correction.py          # all correction logic (~700 lines)
        ├── summary.py             # generate_summary
        ├── state.py               # validator, all_ops, save_progress, etc.
        ├── templates.py           # make_template (split by mode)
        ├── conversation.py        # talk_to_user, main_processor
        └── keywords.py            # describe_action + keyword thread
```

## Step-by-step (each step is one commit)

| # | Step                                                                 | Risk   | Verifies                          |
|---|----------------------------------------------------------------------|--------|-----------------------------------|
| 0 | Scaffold dirs, write `PUBLIC_API.md`, write this file                | none   | n/a                               |
| 1 | Extract `app/config.py` (env vars, constants). Import in server.py   | low    | server.py boots, /health OK       |
| 2 | Extract `app/logging.py` (logger setup, replace `print`)             | low    | logs visible                      |
| 3 | Extract `app/db/mongo.py` + `serializers.py`                         | low    | /api/users still works            |
| 4 | Extract `app/forms/{repository,progress,titles,attachments}.py`      | medium | form GET endpoints work           |
| 5 | Extract `app/audio/{tts,stt,streaming,files}.py`. Gate debug saves.  | medium | WS audio round-trip works         |
| 6 | Extract `app/interview/{helpers,messaging,lifecycle}.py`             | medium | WS conversational turn works      |
| 7 | Extract `app/routes/{health,users,forms}.py`, mount via APIRouter    | low    | all HTTP routes return same shape |
| 8 | Split `src/llm/functionalities.py` → `src/agent/*`. HealthAgent      | HIGH   | full interview happy-path works   |
|   | becomes a thin facade re-exposing every public method.               |        |                                   |
| 9 | Split the 1044-line WebSocket handler into stage modules             | HIGH   | every inbound msg type still works|

## Verification per step

A smoke test script (`tests/smoke.py`) will be added that:

- starts the server (staging port 8001)
- hits `/health`, expects 200
- hits `/api/users`, expects a list shape
- opens a WS, sends `start_interview`, expects a `text_message` reply
- sends a `text_input`, expects another `text_message`
- sends `end_session`, expects a clean close

This is not a unit-test suite — it's a "does the integration still work?" canary.

## Out of scope for this refactor (separate PRs)

- Whisper → faster-whisper / Google Speech
- sentence-transformers → Gemini embeddings
- CPU-only torch wheel
- TTS cache eviction policy
- `audio_files`/`transcripts` retention policy
- ARM (Graviton) image rebuild for t4g.medium

## Decisions pending from user

- [ ] Transcripts retention: kill / flag / S3
- [ ] Verification path: staging on EC2:8001 vs local Python env
