"""Optional Prometheus metrics; every metric is a no-op when the libraries are absent."""

try:
    from prometheus_client import Gauge, make_asgi_app
    from starlette_prometheus import PrometheusMiddleware
    WS_CONNECTIONS = Gauge('ws_active_connections', 'Active WebSocket sessions')
    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False
    class _Noop:
        def inc(self, *a, **k): pass
        def dec(self, *a, **k): pass
        def observe(self, *a, **k): pass
        def labels(self, *a, **k): return self
    WS_CONNECTIONS = _Noop()


def install_metrics(app) -> None:
    """Add request metrics middleware and mount ``/metrics`` when Prometheus is available."""
    if not HAS_PROMETHEUS:
        return
    app.add_middleware(PrometheusMiddleware)
    app.mount("/metrics", make_asgi_app())
