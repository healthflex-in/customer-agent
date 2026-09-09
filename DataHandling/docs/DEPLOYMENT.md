# Deployment Artifacts and Status

This repository contains deployment artifacts, but it does not contain a
verified CI/CD release pipeline or a complete environment-independent production
definition. Promotion must use the owning team’s approved infrastructure,
secrets manager, identity boundary, rollback procedure, and non-production
validation.

## Current artifacts

| File | Current role |
|---|---|
| root `docker-compose.yml` | Supported local Compose entry point; builds `DataHandling/deployment/Dockerfile`, loads ignored `DataHandling/.env`, bind-mounts runtime/source directories, and publishes port 8000. |
| `deployment/Dockerfile` | Python 3.12/Uvicorn backend image. It currently includes build tools in the runtime layer and is tracked under IMG-01 for slimming/reproducibility. |
| `deployment/docker-compose.yml` | Standalone backend Compose definition. Validate its environment inputs before use. |

There is no `deploy.sh`, `docker-compose.dev.yml`, status script, or cleanup
script in the current tree.

## Local image verification

From the repository root:

```bash
docker compose config --quiet
docker compose build healthflex-agent
docker compose up -d healthflex-agent
docker compose ps
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

Run the backend and frontend gates listed in [LOCAL_DEV.md](LOCAL_DEV.md) before
promotion. Provider/database integration checks must use a non-production
environment.

## Required deployment inputs

- non-production/production-specific MongoDB URI and CA policy;
- Gemini API key and approved model availability/quota;
- Google service-account credentials if Speech fallback is enabled;
- explicit S3 credentials, bucket, and regions plus Bedrock model/profile if
  report processing is enabled;
- frontend HTTP/WSS/consent endpoints for the exact environment;
- access-token signing secret (at least 32 bytes), issuer, audience, and a
  trusted clinician-link issuance workflow;
- observability hashing key, metrics scraper, pricing catalog, dashboards, and
  alerts;
- proxy TLS, body-size, timeout, and WebSocket upgrade configuration;
- staging validation of the implemented authentication/authorization contract;
- an approved clinical escalation policy plus ownership, notification,
  acknowledgement/escalation SLA, and monitoring for the
  `MONGO_CLINICAL_ESCALATIONS_COLLECTION` work queue. The local deterministic
  safety floor is not sufficient clinical sign-off by itself.
- approved PROM instrument versions, languages, licences, exact question-bank
  wording/options, electronic-administration approval, and reviewed scoring and
  missing-data rules. Do not expose a PROM score while the stored snapshot says
  `scoring.status: not_configured`.

Secrets and credential files must not be baked into the image, copied by update
archives, printed in logs, or committed to Git.

## Repository hygiene

Commit source, migrations/configuration schemas, dependency manifests, safe
examples, tests, and maintained documentation. Do not commit real environment
files, credentials, session databases, runtime uploads/audio/transcripts,
vector stores, logs, coverage output, caches, build output, IDE state, or local
Compose overrides. Root `.gitignore` and `.dockerignore` enforce these classes.
Before opening a change, run `git status --short`, `git diff --check`, and review
every untracked file rather than adding the repository wholesale.

## Release automation

The former host-specific SSH/rsync update scripts were removed. They embedded a
single host, user, directory, key filename, and container assumptions, and one
script updated development and production from the same working tree. Those
properties made accidental cross-environment or partial deployments possible.

Replace them with a reviewed CI/CD workflow that builds an immutable image once,
runs automated gates, scans it, promotes the same digest between environments,
injects secrets at runtime, performs readiness checks, and supports rollback.
That work remains pending under IMG-01 and deployment governance; this repository
does not claim that a production release path exists until it is implemented and
validated by the owning infrastructure team.
