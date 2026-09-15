# Local Development

Use non-production services and synthetic patient records only. The repository
does not contain a test database or provider emulator. The customer-agent relies
on the existing external consent/OTP flow and does not require its own access
token. Use only synthetic users whose consent state is appropriate for the test.

## Repository layout

```text
customerAgent/
├── customer-agent/              backend repository and Compose project
│   ├── docker-compose.yml
│   └── DataHandling/            Python application root
└── customer-agent-frontend/     React/Vite frontend repository
```

Run backend Compose commands from `customer-agent/`, not from `DataHandling/`.

## Prerequisites

- Docker Engine with the Compose plugin;
- permission to access the Docker daemon;
- a non-production MongoDB database containing a test user;
- a Gemini API key;
- Node.js 20 or newer for frontend work;
- optional Google Cloud Speech, AWS S3/Bedrock, Langfuse, and MCP credentials
  only when testing those features.

If Docker reports permission denied for `/var/run/docker.sock`, correct the host
Docker installation/group membership or run Docker through the organization’s
approved privilege workflow. This is a host permission problem, not an
application WebSocket problem.

## Backend configuration

Secrets are ignored by Git and are not supplied by the repository.

```bash
cp DataHandling/.env.example DataHandling/.env
```

At minimum configure:

- `MONGO_URI` for a non-production database;
- `GEMINI_API_KEY`;
- `OBSERVABILITY_HASH_KEY` if pseudonymous cross-event correlation is required.

For audio fallback, place an untracked Google service-account JSON file at
`DataHandling/config/google_key.json`; Compose exposes it through
`GOOGLE_APPLICATION_CREDENTIALS`. Gemini uses `GEMINI_API_KEY`, not this JSON.

For report uploads/summaries, configure the S3 bucket, explicit AWS access key
and secret currently required by `upload/s3_client.py`, AWS region, and Bedrock
model/profile. Ensure both services contain no production data during development.

Never reuse a production `.env` merely to make local startup succeed.

Urgent-risk tests may create metadata-only records in the configured
`MONGO_CLINICAL_ESCALATIONS_COLLECTION`. Use a non-production database and do
not test safety rules with a real patient identity.

For non-default assessment forms, use synthetic question-bank records with a
stable unique ID and explicit response type/options for every item. Recognized
clinical `prom_*` questions are immutable. New records store a `promSnapshot`
with the exact administered definition and stable-ID answers; scoring remains
disabled until an approved instrument contract is supplied.

## Start the backend

From `customer-agent/`:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f healthflex-agent
```

The backend is published at `http://localhost:8000`. The immutable image starts
one Uvicorn process without `--reload`. Rebuild after Python or dependency edits:

```bash
docker compose up -d --build healthflex-agent
```

Check liveness and metrics:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

`/health` reports local initialization state. It does not call Gemini, Google,
AWS, or MongoDB on every request and therefore is not proof of provider access.

## Start the frontend

From the sibling frontend repository:

```bash
cd ../customer-agent-frontend
cp .env.example .env.local
npm install
npm run dev
```

The local template uses:

```dotenv
VITE_APP_ENV=local
VITE_API_URL=http://localhost:8000
VITE_WS_URL=ws://localhost:8000
```

All `VITE_*` values are public browser configuration. Never put a server secret
in them. The configuration validator requires pathless API/WebSocket bases,
localhost endpoints for local mode, HTTPS/WSS in deployed modes, and rejects
known cross-environment hosts.

The patient page uses the existing user/form link shape:

```text
http://localhost:8080/{userId}/{formId}
```

Use only a synthetic user ID that exists in the configured non-production
MongoDB database. `FRM-01` selects the standard intake; other form IDs attempt to
load assigned PROM questions. The frontend checks consent through the backend;
the external consent application is responsible for its OTP verification and
stored consent record.

## Verification

Host Python can run the dependency-light tests:

```bash
cd DataHandling
python3 -m compileall -q app docscanner src server.py
python3 -m unittest discover -s tests
```

Some optional exporter/integration assertions may skip when their dependency is
not installed on the host. Run the same suite in the built image for the
dependency-complete result.

The reviewed direct dependencies live in `requirements.in`; both local and
container installs consume the fully pinned `requirements-docker.txt` lock. To
update that lock deliberately with Python 3.12:

```bash
python3.12 -m venv .venv-lock
.venv-lock/bin/python -m pip install pip-tools==7.5.1
.venv-lock/bin/pip-compile --strip-extras --allow-unsafe \
  --output-file requirements-docker.txt requirements.in
```

Review the lock diff and run the complete suite before committing it. Do not
edit transitive versions directly in the generated file.

With the backend running, the smoke test verifies health, the users route, and
the WebSocket missing-user validation path without creating a patient intake:

```bash
cd DataHandling
python3 tests/smoke.py
```

To target another local backend:

```bash
BASE_URL=http://localhost:8001 python3 tests/smoke.py
```

Frontend gates:

```bash
cd ../customer-agent-frontend
npx tsc -b
npm run lint
npm run build
npm audit
```

## Safe manual test

1. Create or obtain a synthetic user in the non-production database.
2. Open `http://localhost:8080/{syntheticUserId}/FRM-01`.
3. Confirm the UI status becomes connected.
4. Submit synthetic text such as “I have test knee discomfort since yesterday.”
5. Confirm a question arrives and progress persists only under that test user.
6. Test microphone/report flows only with synthetic audio/documents.

Do not use a guessed all-zero ID against a shared database. The server validates
that a user exists, and a production connection could still mutate real records.

## Stop the stack

```bash
docker compose down
```

Runtime directories under `DataHandling/` are bind-mounted. Review them before
deleting anything; they may contain local reports, audio, or database files.

## Common failures

### `DataHandling/.env not found`

Create it from the template and fill non-production values:

```bash
cp DataHandling/.env.example DataHandling/.env
```

### Browser shows “Access via your link”

The root page is intentionally not an intake. Open a valid
`/{userId}/{formId}` URL.

### WebSocket remains offline

Check, in order:

1. `docker compose ps` and backend logs;
2. `curl http://localhost:8000/health`;
3. frontend `VITE_WS_URL=ws://localhost:8000` and restart Vite after changes;
4. browser developer-tools Network → WS handshake/status;
5. that the URL user exists in the configured non-production database;
6. that the link contains a current access token with `interview:write` scope
   and that backend issuer/audience/secret values match the token issuer.

### Report upload fails

Confirm S3 configuration, target-region access, report queue initialization,
Bedrock model/profile access, and the configured file/page limits. An accepted
upload returns a queued job; summary completion is asynchronous.
