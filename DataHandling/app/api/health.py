"""Process health for Docker and monitoring."""

from collections.abc import Callable

from app.db.mongo import mongo
from app.observability.ai_usage import PRICING_VERSION
from app.observability.metrics import HAS_PROMETHEUS


def register_health_routes(app, graph_ready: Callable[[], bool]) -> None:
    """Register /health. graph_ready is read on each request so startup can finish first.

    Routes are attached to the application itself. The Prometheus middleware
    reads route.path, which nested included routers do not expose.
    """

    @app.get("/health")
    async def health_check():
        """Health check endpoint for Docker and monitoring."""
        return {
            "status": "ok",
            "service": "healthflex-customer-agent",
            "components": {
                "mongodb": "ok" if mongo.customer_info_collection is not None else "degraded",
                "graph": "ok" if graph_ready() else "degraded",
                "stt": "gemini-primary-google-cloud-speech-fallback",
            },
            "ai_telemetry": {
                "metrics_endpoint": "/metrics" if HAS_PROMETHEUS else None,
                "pricing_version": PRICING_VERSION,
            },
        }
