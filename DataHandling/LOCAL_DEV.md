# Local development

Local-first workflow: run the full container stack on your laptop, refactor,
smoke-test, *then* push to EC2.

## Prerequisites

- Docker Desktop running (Apple Silicon / Intel — same image works).
- `DataHandling/.env` present (already checked in for this repo; matches EC2).
- `DataHandling/config/*.json` Google service-account keys (already present).

## Boot the stack

From the **`healthflex-agent/`** directory (one level above `DataHandling/`):

```bash
docker compose up -d --build       # first time: ~10–15 min (torch, whisper, etc.)
docker compose logs -f             # watch the boot — Whisper + HealthAgent take ~30–60s
```

First boot will:
1. Build the image from `DataHandling/deployment/Dockerfile`.
2. Download the Whisper `base` model (~150 MB) into the container.
3. Initialize ChromaDB at `/app/db/vector/...` (persisted via bind mount).
4. Initialize `HealthAgent` (LlamaIndex + Gemini wiring).

Server is ready when logs show `Application startup complete.` and the container
is marked `(healthy)` by `docker ps`.

## Smoke test

```bash
cd DataHandling
pip install requests websockets       # one-time, host-side
python tests/smoke.py
```

Expected output:

```
Smoke test against http://localhost:8000
— GET /health
  ✓ 200 OK — body: ...
— GET /api/users
  ✓ 200 — ...
— WebSocket /ws/<client_id> — start_interview round trip
  ✓ connected to ws://localhost:8000/ws/smoke-...
  ✓ sent start_interview
  ✓ received text_message — ...
  ✓ sent end_session, closing

ALL CHECKS PASSED
```

A failing smoke test = refactor regression. Revert the last commit.

## Hot-reload during refactor

`docker-compose.yml` bind-mounts `./DataHandling` → `/app`, so file edits are
visible to the container immediately. **But uvicorn is not started with
`--reload`**, so the Python process won't pick up changes until you restart:

```bash
docker compose restart        # ~30s to reload Whisper + HealthAgent
```

If you want true hot-reload during refactor, edit the Dockerfile CMD
temporarily:

```dockerfile
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

(Don't ship this to prod — `--reload` doubles memory.)

## Tearing down

```bash
docker compose down            # stop and remove the container
docker compose down -v         # ...and delete named volumes (won't touch bind mounts)
```

## Pushing to EC2 (only after local smoke test passes)

Use the existing scripts in `DataHandling/deployment/`:

- `update-fast.sh` — rsync code + `docker compose restart` (no rebuild)
- `update.sh` — rsync + rebuild (use when requirements change)

Always run `tests/smoke.py` locally before either script.
