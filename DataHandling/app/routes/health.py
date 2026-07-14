"""GET /health endpoint."""
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
async def health_check():
    from app.db.mongo import get_customer_info_collection
    col = get_customer_info_collection()
    from src.graph.graph import build_interview_graph
    return {
        "status": "ok",
        "service": "healthflex-customer-agent",
        "components": {
            "mongodb": "ok" if col is not None else "degraded",
        }
    }
