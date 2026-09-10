"""FastAPI entrypoint for the assessment test recommender.

Self-contained on the customer-agent EC2. The dashboard's only touchpoint is a
thin FE "customer" icon that calls GET /recommendations. PHI crosses the wire →
CORS-locked + optional shared-secret auth (RECOMMENDER_API_KEY).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app import config, db, service

app = FastAPI(title="Assessment Test Recommender", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.on_event("startup")
def _startup() -> None:
    db.init_mongo()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mongo": db.is_connected()}


@app.get("/recommendations", dependencies=[Depends(require_api_key)])
def recommendations(
    userId: str = Query(...),
    appointmentId: str = Query(...),
    reportId: str | None = Query(default=None),
    refresh: bool = Query(default=False),
) -> dict:
    try:
        return service.get_recommendations(userId, appointmentId, reportId, force=refresh)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/recommendations/refresh", dependencies=[Depends(require_api_key)])
def refresh(userId: str = Query(...), appointmentId: str = Query(...),
            reportId: str | None = Query(default=None)) -> dict:
    return service.get_recommendations(userId, appointmentId, reportId, force=True)
