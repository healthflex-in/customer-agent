"""Structured JSON logging with a print-based fallback when structlog is absent."""

try:
    import structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.contextvars.merge_contextvars,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    log = structlog.get_logger()
except ImportError:
    class _FallbackLog:
        def info(self, event, **kw): print(f"[INFO] {event}", kw or "")
        def warning(self, event, **kw): print(f"[WARN] {event}", kw or "")
        def error(self, event, **kw): print(f"[ERROR] {event}", kw or "")
    log = _FallbackLog()
