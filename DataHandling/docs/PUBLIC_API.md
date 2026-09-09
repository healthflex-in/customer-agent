# Current HTTP and WebSocket Contract

Verified against `server.py` and the frontend `useWebSocket` consumer on
3 September 2026. This describes the current implementation, including known
limitations; it is not a promise that unsafe behavior must be preserved.

## Security boundary

Every patient-data HTTP route requires an `Authorization: Bearer <token>`
header. The first WebSocket `start_interview` message requires the same signed
token in `accessToken`; unauthenticated control/audio messages are rejected and
the socket is closed with policy code `1008`. `/health`, framework API docs,
and metadata-only metrics remain public.

Tokens are short-lived HS256 JWTs with strict `iss`, `aud`, `sub`, `role`,
`scope`, `iat`, and `exp` validation. Patient tokens can access only the patient
whose ID equals `sub`. Clinician tokens require an explicit `patients` allow-
list, while admin tokens can address any patient but still require the operation
scope. The user directory additionally requires clinician/admin role plus
`users:read`. The server rejects missing/short signing secrets, unsupported
algorithms/roles, invalid signatures, future/expired/excessively long tokens,
and wrong issuer/audience values. There is deliberately no public token-
issuance endpoint.

The API has no question/category administration routes. It cannot add, edit,
publish, categorise, or delete clinical questions.

## HTTP endpoints

FastAPI also exposes its default `/docs`, `/redoc`, and `/openapi.json` pages.
When Prometheus dependencies are installed, metrics are mounted at `/metrics`.

| Method | Path | Input | Successful response |
|---|---|---|---|
| `GET` | `/health` | None | Service/component state and AI telemetry pricing version. This is liveness/configuration state, not an external-provider probe. |
| `GET` | `/api/users` | Bearer token with clinician/admin `users:read`; query `search`, `limit` | `{ "users": [...] }`, sorted by `updatedAt` descending. |
| `GET` | `/api/users/{user_id}/consent` | Patient-bound Bearer token with `consent:read` | Consent state from `users.profileData` or active `consentrecords`. |
| `POST` | `/api/users/{user_id}/consent` | Patient-bound Bearer token with `consent:write` | Marks consent accepted with policy version `1.1.0`. |
| `GET` | `/api/users/{user_id}/forms` | Patient-bound Bearer token with `forms:read` | `{ "forms": [...] }`. |
| `GET` | `/api/forms/{form_id}` | Patient-bound `forms:read`; required `userId`, optional `attemptId` | `{ "form": {...} }` for the exact/latest attempt. |
| `GET` | `/api/forms/{form_id}/progress` | Patient-bound `forms:read`; required `userId`, optional `attemptId` | Completion state for the selected/latest attempt. |
| `POST` | `/api/forms/{form_id}/attachments` | Patient-bound `forms:write`; multipart `files`, `userId`, optional `attemptId` | Attachment/job/form/progress state. |

### Attachment processing

The upload handler:

1. resolves the exact user/form pair;
2. enforces configured file-count, per-file, total-byte, file-signature,
   extension/MIME, decoder, image-dimension, PDF-page, and total-page limits;
3. validates the full batch before creating S3 objects;
4. stores attachment metadata;
5. enqueues a Mongo-backed report-summary job;
6. returns before Bedrock processing completes.

The latest valid job publishes the Bedrock summary into the form. Upload success
therefore does not mean OCR/summary completion. There is not yet a dedicated job
status endpoint; the client observes subsequent form state.

Default application limits are five files, 15 MB per file, 25 MB combined, and
five report pages combined. Deployment/proxy request limits may be stricter.

## WebSocket endpoint

WebSocket endpoint: `/ws/{client_id}`.

Connect to:

```text
/ws/{client_id}
```

`client_id` is used only for connection correlation and is not identity. After
connecting, send authenticated `start_interview`; the verified claims, not the
path or supplied patient ID alone, authorize the session.

### Client-to-server messages

Control messages are UTF-8 JSON. Recorded audio chunks are binary frames.

| Type | Fields | Behavior |
|---|---|---|
| `start_interview` | `accessToken` and `userId` required; `formId`/`attemptId` optional | Verifies signature, claims, scope, and patient binding before any database lookup, then starts/resumes the intake. |
| `start_new_form` | `userId` required if not already associated | Clears all regular/PROM/graph session state and allocates a new opaque attempt ID without creating an empty database document. |
| `load_form` | `formId` required; `attemptId` optional | Loads the exact attempt for the session user, or the latest matching attempt for an older client. |
| `text_input` | `text`; optional `requestId`, `questionId`, `inputMode` | Processes one typed/transcribed answer. `inputMode: "structured_prom"` is accepted only when its validated metadata matches the active PROM question(s). |
| `audio_start` | Timestamp is accepted but not trusted | Opens a server-timed, size-limited recording window. |
| binary frame | Raw browser audio bytes | Appended only while recording; cumulative bytes and elapsed time are bounded. |
| `audio_end` | Optional declared `duration` | Transcribes accumulated audio. The server sends the transcript but does not submit it as an interview answer automatically. |
| `end_session` | Optional `userId` | Re-authorizes any supplied patient ID, saves current state, and sends confirmation. |

`text_input.requestId` must contain 1–128 characters from
`A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, or `-`. Reusing the same request ID and
question returns a duplicate acknowledgement; reusing it for another question
returns an error. This window is bounded and process-local.

### Server-to-client messages

| Type | Important fields | Meaning |
|---|---|---|
| `text_message` | `text`, `session_id`, `interview_state`, `request_attachment`, optional `question_meta` | Primary assistant/welcome/question/summary response. |
| `token` | `content` | Incremental display token where the graph path can provide it. Some paths synthesize tokens after a complete response, so this does not always indicate provider streaming. |
| `thought_update` | `thoughts[]` containing `stage`, `detail`, `status` | UI progress labels for graph processing stages; not model chain-of-thought. |
| `transcription` | `text`, `timestamp` | Gemini transcription or Google Speech fallback result after `audio_end`. |
| `form_loaded` | `text`, `session_id`, `interview_state`, `form_data` | Confirmation/state sent by `load_form`; normally followed by `text_message`. |
| `submission_ack` | `status: "duplicate"`, `requestId` | A retry was recognized and not processed again. |
| `clinical_escalation` | `text`, `category`, `severity`, `policyVersion`, `stopInterview` | Deterministic urgent-risk response. The server then closes with code `4003`; clients must display the message, stop intake, and must not reconnect automatically. |
| `error` | `text` or `message` | Validation/provider/processing failure. Both keys currently exist; clients must handle either until the protocol is normalized. |
| `system` | `message` | Operational notice, currently used during graceful server shutdown. |

`interview_state` currently contains:

```json
{
  "section": "Present Complaint",
  "progress": 25,
  "missing_fields": [],
  "attachments": [],
  "formId": "FRM-01",
  "attemptId": "ATT-70a7da1a-cb4d-4936-b6da-b82e094f280f",
  "promSteps": null
}
```

PROM `question_meta` can describe a single or batched input using `type`,
`options`, `question_id`, `question_ids`, `questions`, `question_options`,
`question_types`, and `question_scales`. Supported frontend presentation types
include text, single/multiple choice, scale/rating, boolean, number, date/time,
grid, upload, and `multi_answer` variants.

Clinical PROM templates must provide a stable unique question ID and explicit
response type/options for every item. Recognized `prom_*` items retain their
source wording and recall window; they are not personalized. An optional
`instrumentVersion` may be supplied by the question-bank record.

## Main state and persistence behavior

- A separate `HealthAgent` instance is constructed per WebSocket connection.
- LangGraph is the primary interview path; `HealthAgent.main_processor` remains
  the fallback.
- MongoDB `customer-info` is persistent form storage.
- `formId` identifies the questionnaire/template and `attemptId` identifies one
  intake submission. Legacy records without an attempt ID remain readable and
  updateable, while every explicit new intake receives an independent ID.
- Forms transition monotonically through `draft`, `in_progress`, and
  `completed`; ordinary saves cannot downgrade a completed record.
- Only drafts receive `expiresAt` for TTL cleanup.
- Report jobs use the `customer-agent-report-jobs` MongoDB collection.
- Clinical safety events use `customer-agent-clinical-escalations`; events omit
  the triggering answer and begin with status `detected`.
- Session/checkpointer state, rate limiting, and request-id tracking are local to
  one process.

New clinical assessment records also contain `promSnapshot` schema version 1.
It separates the exact administered definition from stable-question-ID
responses and includes a content hash. Its scoring state remains
`not_configured` until an approved instrument/version-specific scoring contract
is implemented; consumers must not calculate or display a score from the legacy
text-keyed `form_data` view.

## External services

- Gemini 2.5 Flash-Lite: general interview text by default.
- Gemini 2.5 Flash: reasoning extraction and primary audio transcription by default.
- Google Cloud Speech-to-Text v1: audio fallback.
- Amazon S3: report objects.
- Amazon Bedrock Nova: multimodal report summarization.
- MongoDB: users, consent records, forms, and report jobs.
- Prometheus: metadata-only operational metrics.
- Langfuse/MCP: optional and disabled for patient content unless explicitly
  configured and approved.

Model IDs are validated centrally and may be changed only to approved registry
values through environment configuration.
