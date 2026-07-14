"""Shared active connection set + Prometheus gauge."""
_connections: set = set()
active_connections = _connections

class _Noop:
    def inc(self): pass
    def dec(self): pass

try:
    from prometheus_client import Gauge
    ws_connections_gauge = Gauge("ws_active_connections", "Active WebSocket sessions")
except Exception:
    ws_connections_gauge = _Noop()
